"""Calibrate the trained model and validate it on the held out test split.

Everything that is tuned is tuned on the validation split only:

    * whether test time augmentation helps
    * the softmax temperature, fitted by minimising negative log likelihood
    * the referral threshold, set to reach a target sensitivity

The test split is then scored once with those settings frozen, and the
results are written to artifacts/evaluation_report.json and rendered as
docs/VALIDATION_REPORT.md. The report is generated rather than written by
hand so that its numbers always describe the model actually on disk.

Usage:
    python scripts/calibrate.py [--target-sensitivity 0.95]
"""

import argparse
import csv
import json
import math
import os
import time
from collections import Counter, defaultdict

import numpy as np
from PIL import Image

from common import ARTIFACTS, normalized_path, source_of
from ear_detection import config
from ear_detection.model import Classifier, assess, softmax
from report import REPORT_HTML, REPORT_MD, write_reports

TEMPERATURE_GRID = np.round(np.arange(0.25, 5.001, 0.01), 2)
OPERATING_TARGETS = [0.90, 0.93, 0.95, 0.97, 0.99]
ECE_BINS = 15


def wilson(successes, total, z=1.96):
    """Wilson score interval for a binomial proportion."""
    if total == 0:
        return (float("nan"), float("nan"))
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def auroc(scores_positive, scores_negative):
    """Area under the ROC curve via the Mann Whitney statistic."""
    pos = np.asarray(scores_positive)
    neg = np.asarray(scores_negative)
    if pos.size == 0 or neg.size == 0:
        return float("nan")
    combined = np.concatenate([pos, neg])
    order = combined.argsort()
    ranks = np.empty_like(order, dtype=np.float64)
    ranks[order] = np.arange(1, combined.size + 1)
    # average ranks for ties
    for value in np.unique(combined):
        tie = combined == value
        if tie.sum() > 1:
            ranks[tie] = ranks[tie].mean()
    return float((ranks[: pos.size].sum() - pos.size * (pos.size + 1) / 2) / (pos.size * neg.size))


def nll(logits, truth, temperature):
    scaled = logits / temperature
    scaled = scaled - scaled.max(axis=1, keepdims=True)
    log_probs = scaled - np.log(np.exp(scaled).sum(axis=1, keepdims=True))
    return float(-log_probs[np.arange(len(truth)), truth].mean())


def probabilities(logits, temperature):
    return np.stack([softmax(row / temperature) for row in logits])


def expected_calibration_error(probs, truth, bins=ECE_BINS):
    confidence = probs.max(axis=1)
    correct = probs.argmax(axis=1) == truth
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        in_bin = (confidence > lo) & (confidence <= hi)
        if in_bin.any():
            total += in_bin.mean() * abs(correct[in_bin].mean() - confidence[in_bin].mean())
    return float(total)


def macro_f1(pred, truth, n):
    f1s = []
    for c in range(n):
        tp = np.sum((pred == c) & (truth == c))
        fp = np.sum((pred == c) & (truth != c))
        fn = np.sum((pred != c) & (truth == c))
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * p * r / (p + r) if p + r else 0.0)
    return float(np.mean(f1s)), f1s


def collect_logits(classifier, rows):
    """Per view logits for every image: shape (n, 8, classes)."""
    from ear_detection import imaging

    out = np.zeros((len(rows), 8, len(classifier.classes)), dtype=np.float32)
    started = time.time()
    for i, row in enumerate(rows):
        array = np.asarray(Image.open(normalized_path(row["path"])).convert("RGB"), dtype=np.uint8)
        feats = classifier.features(imaging.dihedral_views(array))
        out[i] = classifier.logits_from_features(feats)
        if (i + 1) % 200 == 0:
            print("    %d / %d  (%.0fs)" % (i + 1, len(rows), time.time() - started), flush=True)
    return out


def referral_threshold(disease_scores, target):
    """Highest threshold whose sensitivity on these scores meets the target."""
    ordered = np.sort(disease_scores)
    # Allow at most floor((1 - target) * n) misses.
    allowed_misses = int(math.floor((1 - target) * ordered.size + 1e-9))
    return float(ordered[allowed_misses]) if ordered.size else 0.5


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-sensitivity", type=float, default=0.97)
    parser.add_argument("--model", default=config.DEFAULT_MODEL_PATH, help="model to calibrate")
    parser.add_argument("--out-dir",
                        help="write calibration and evaluation here instead of artifacts/, "
                             "and leave the published report untouched (used for candidates)")
    args = parser.parse_args()
    calibration_out = os.path.join(args.out_dir, "calibration.json") if args.out_dir         else config.CALIBRATION_FILE
    evaluation_out = os.path.join(args.out_dir, "evaluation_report.json") if args.out_dir         else config.EVALUATION_FILE

    with open(config.LABELS_FILE, encoding="utf-8") as fh:
        labels = json.load(fh)
    classes = labels["classes"]
    index = labels["class_to_index"]
    n_classes = len(classes)
    normal = index[config.NORMAL_CLASS]

    with open(os.path.join(ARTIFACTS, "manifest.csv"), newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    val_rows = [r for r in rows if r["split"] == "val"]
    test_rows = [r for r in rows if r["split"] == "test"]
    y_val = np.array([index[r["label"]] for r in val_rows])
    y_test = np.array([index[r["label"]] for r in test_rows])

    classifier = Classifier(args.model, classes, temperature=1.0, use_tta=True)

    print("scoring validation split (%d images x 8 views)" % len(val_rows), flush=True)
    val_views = collect_logits(classifier, val_rows)
    print("scoring test split (%d images x 8 views)" % len(test_rows), flush=True)
    test_views = collect_logits(classifier, test_rows)

    # ---- decide on test time augmentation, on validation only ----
    val_tta, val_single = val_views.mean(axis=1), val_views[:, 0]
    f1_tta, _ = macro_f1(val_tta.argmax(1), y_val, n_classes)
    f1_single, _ = macro_f1(val_single.argmax(1), y_val, n_classes)
    use_tta = f1_tta >= f1_single
    print("val macro F1  single view %.4f   8 view average %.4f  -> use_tta=%s"
          % (f1_single, f1_tta, use_tta), flush=True)

    val_logits = val_tta if use_tta else val_single
    test_logits = test_views.mean(axis=1) if use_tta else test_views[:, 0]

    # ---- temperature, on validation only ----
    losses = [nll(val_logits, y_val, t) for t in TEMPERATURE_GRID]
    temperature = float(TEMPERATURE_GRID[int(np.argmin(losses))])
    print("temperature %.2f  (val NLL %.4f -> %.4f)"
          % (temperature, nll(val_logits, y_val, 1.0), min(losses)), flush=True)

    p_val = probabilities(val_logits, temperature)
    p_test = probabilities(test_logits, temperature)
    p_test_raw = probabilities(test_logits, 1.0)

    # ---- referral threshold, on validation only ----
    val_disease = 1.0 - p_val[:, normal]
    val_is_disease = y_val != normal
    threshold = referral_threshold(val_disease[val_is_disease], args.target_sensitivity)

    # ---- frozen settings, scored once on test ----
    test_disease = 1.0 - p_test[:, normal]
    test_is_disease = y_test != normal
    refer = test_disease >= threshold

    tp = int(np.sum(refer & test_is_disease))
    fn = int(np.sum(~refer & test_is_disease))
    tn = int(np.sum(~refer & ~test_is_disease))
    fp = int(np.sum(refer & ~test_is_disease))

    referral = {
        "threshold": threshold,
        "target_sensitivity": args.target_sensitivity,
        "sensitivity": tp / (tp + fn),
        "sensitivity_ci": wilson(tp, tp + fn),
        "specificity": tn / (tn + fp),
        "specificity_ci": wilson(tn, tn + fp),
        "ppv": tp / (tp + fp) if tp + fp else float("nan"),
        "npv": tn / (tn + fn) if tn + fn else float("nan"),
        "auroc": auroc(test_disease[test_is_disease], test_disease[~test_is_disease]),
        "tp": tp, "fn": fn, "tn": tn, "fp": fp,
    }

    operating_points = []
    for target in OPERATING_TARGETS:
        t = referral_threshold(val_disease[val_is_disease], target)
        r = test_disease >= t
        operating_points.append({
            "target": target,
            "threshold": t,
            "test_sensitivity": float(np.mean(r[test_is_disease])),
            "test_specificity": float(np.mean(~r[~test_is_disease])),
        })

    missed = Counter(classes[c] for c in y_test[~refer & test_is_disease])

    pred = p_test.argmax(axis=1)
    test_macro, f1s = macro_f1(pred, y_test, n_classes)

    per_class = []
    for c in range(n_classes):
        support = int(np.sum(y_test == c))
        tpc = int(np.sum((pred == c) & (y_test == c)))
        fpc = int(np.sum((pred == c) & (y_test != c)))
        per_class.append({
            "class": classes[c],
            "name": config.CLASS_INFO.get(classes[c], (classes[c], ""))[0],
            "support": support,
            "precision": tpc / (tpc + fpc) if tpc + fpc else 0.0,
            "recall": tpc / support if support else 0.0,
            "recall_ci": wilson(tpc, support),
            "f1": f1s[c],
        })

    coarse_of = {}
    for parent, members in labels.get("parent_groups", {}).items():
        for m in members:
            coarse_of[m] = parent
    coarse = [coarse_of.get(c, c) for c in classes]
    coarse_accuracy = float(np.mean([coarse[a] == coarse[b] for a, b in zip(pred, y_test)]))

    # Source stratified recall, for classes present in more than one source.
    by_class_source = defaultdict(lambda: [0, 0])
    for row, p, t in zip(test_rows, pred, y_test):
        key = (classes[t], source_of(row["path"]))
        by_class_source[key][1] += 1
        by_class_source[key][0] += int(p == t)
    stratified = []
    for cls in classes:
        sources = sorted(s for (c, s) in by_class_source if c == cls)
        if len(sources) > 1:
            stratified.append({
                "class": cls,
                "by_source": {s: {"correct": by_class_source[(cls, s)][0],
                                  "total": by_class_source[(cls, s)][1]} for s in sources},
            })

    margins_entropies = [assess(p) for p in p_test]
    low_conf = np.array([m < config.CONFIDENCE_MIN_MARGIN or e > config.CONFIDENCE_MAX_ENTROPY_RATIO
                         for m, e in margins_entropies])
    correct = pred == y_test

    confusion = [[int(np.sum((y_test == t) & (pred == p))) for p in range(n_classes)]
                 for t in range(n_classes)]

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "classes": classes,
        "use_tta": bool(use_tta),
        "val_macro_f1": {"single_view": f1_single, "tta": f1_tta},
        "temperature": temperature,
        "calibration": {
            "test_ece_before": expected_calibration_error(p_test_raw, y_test),
            "test_ece_after": expected_calibration_error(p_test, y_test),
            "test_nll_before": nll(test_logits, y_test, 1.0),
            "test_nll_after": nll(test_logits, y_test, temperature),
        },
        "test": {
            "images": int(len(y_test)),
            "macro_f1": test_macro,
            "accuracy": float(np.mean(correct)),
            "balanced_accuracy": float(np.mean([pc["recall"] for pc in per_class])),
            "coarse_accuracy": coarse_accuracy,
            "per_class": per_class,
            "confusion": confusion,
        },
        "referral": referral,
        "operating_points": operating_points,
        "referral_misses": dict(missed),
        "confidence_gate": {
            "flagged_fraction": float(low_conf.mean()),
            "accuracy_confident": float(correct[~low_conf].mean()) if (~low_conf).any() else None,
            "accuracy_flagged": float(correct[low_conf].mean()) if low_conf.any() else None,
        },
        "source_stratified": stratified,
        "split_sizes": dict(Counter(r["split"] for r in rows)),
        "grouping": labels.get("grouping", {}),
    }

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
    with open(calibration_out, "w", encoding="utf-8") as fh:
        json.dump({
            "temperature": temperature,
            "referral_threshold": threshold,
            "target_sensitivity": args.target_sensitivity,
            "use_tta": bool(use_tta),
            "fitted_on": "validation split",
            "generated_at": report["generated_at"],
        }, fh, indent=2)

    with open(evaluation_out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    if not args.out_dir:
        write_reports(report)

    print("\nreferral at threshold %.4f: sensitivity %.3f  specificity %.3f  AUROC %.3f"
          % (threshold, referral["sensitivity"], referral["specificity"], referral["auroc"]))
    print("test macro F1 %.4f   accuracy %.4f" % (test_macro, report["test"]["accuracy"]))
    print("wrote", calibration_out)
    print("wrote", evaluation_out)
    if not args.out_dir:
        print("wrote", REPORT_MD)
        print("wrote", REPORT_HTML)


if __name__ == "__main__":
    main()
