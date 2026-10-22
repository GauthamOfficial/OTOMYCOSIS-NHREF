"""One retraining run, started from the administration page.

    python scripts/retrain.py --job-id N

Steps
1. Collect examinations whose latest review names a trained condition.
   Diagnoses typed as "other" are left out: there is no class to learn them as.
2. Set aside the reserved evaluation cohort: about 30 percent of patients,
   fixed by a hash of the patient id so the same patients are reserved at
   every run and are never trained on. Splitting by patient keeps both ears
   and repeat visits on the same side of the split.
3. Train a candidate on the original training data plus the remaining
   reviewed cases. The original validation and test splits are untouched.
4. Calibrate the candidate on the original validation split and score it on
   the frozen test split, exactly as the current model was.
5. Score current and candidate on the reserved local cases.
6. Apply the gate: the candidate must not be worse on either test set.
7. Register the candidate. It goes live only when an administrator approves.

Any reviewed upload whose content matches a validation or test image is
excluded, so the frozen comparison cannot be contaminated.

Set RETRAIN_QUICK=1 for a one epoch run when testing the pipeline itself.
"""

import argparse
import csv
import hashlib
import os
import subprocess
import sys
import time
import traceback

import numpy as np

from common import BASE_DIR
from ear_detection import config, imaging, retraining, storage
from ear_detection.db import ModelVersion, TrainingJob, db, make_db_app, utcnow
from ear_detection.registry import summarise

QUICK = os.environ.get("RETRAIN_QUICK") == "1"
HOLDOUT_FRACTION = 0.3   # share of patients reserved for independent evaluation
RESERVED_SALT = "otoscan-reserved-cohort-v1"
TOLERANCE_TEST = 0.01     # allowed drop on the frozen test set
TOLERANCE_LOCAL = 0.02    # allowed drop on the reserved local cases
MIN_LOCAL_FOR_GATE = 10   # below this the local comparison is reported, not enforced
CANDIDATES_DIR = os.path.join(BASE_DIR, "models", "candidates")


def log(message):
    print("%s  %s" % (time.strftime("%H:%M:%S"), message), flush=True)


def set_job(job_id, **fields):
    job = db.session.get(TrainingJob, job_id)
    for key, value in fields.items():
        setattr(job, key, value)
    db.session.commit()


def collect_reviewed():
    """Trainable reviewed cases, using the same rule the admin page reports."""
    usable, excluded = retraining.trainable_examinations()
    rows = []
    for exam, label in usable:
        path = storage.absolute(exam.image_path)
        if os.path.isfile(path):
            rows.append({"exam_id": exam.id, "patient_id": exam.patient_id,
                         "path": path, "label": label})
    return rows, excluded


def is_reserved(patient_id):
    """Whether a patient belongs to the reserved evaluation cohort.

    Membership is a stable function of the patient id, not of the run. An
    earlier version shuffled the patient list with the job id as the seed,
    which drew a different holdout every run, so a patient held out once
    could be trained on the next time and no cohort stayed independent of
    the weights. Hashing the id with a fixed salt keeps the cohort unchanged
    as new patients arrive: a reserved patient is reserved for good, and the
    local comparison is always made on cases the candidate never saw.
    """
    digest = hashlib.sha256(("%s:%s" % (RESERVED_SALT, patient_id)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64) < HOLDOUT_FRACTION


def split_by_patient(rows):
    """Training cases and reserved cases, split so one patient never spans both."""
    patients = {r["patient_id"] for r in rows}
    hold = {p for p in patients if is_reserved(p)}
    # Below a handful of patients the reserved share is too small to judge
    # anything, and holding cases back only starves training.
    if len(patients) < 4:
        hold = set()
    return ([r for r in rows if r["patient_id"] not in hold],
            [r for r in rows if r["patient_id"] in hold])


def next_version():
    active = ModelVersion.query.filter_by(status="active").first()
    major = int(active.version.split(".")[0]) if active else 2
    minors = [int(v.version.split(".")[1]) for v in ModelVersion.query.all()
              if v.version.split(".")[0] == str(major)]
    return "%d.%d.0" % (major, max(minors + [0]) + 1)


def run_step(args, label):
    log(label)
    process = subprocess.run([sys.executable] + args, cwd=BASE_DIR)
    if process.returncode != 0:
        raise RuntimeError("%s failed (exit code %d)." % (label, process.returncode))


def evaluate_local(model_path, calibration_path, rows):
    """Accuracy and referral performance of one model on reviewed cases."""
    from ear_detection.model import Classifier

    if not rows:
        return {"cases": 0, "accuracy": None, "sensitivity": None, "specificity": None}
    meta = config.model_metadata(model_path, calibration_path)
    classifier = Classifier(meta["model_path"], meta["classes"],
                            temperature=meta["temperature"], use_tta=meta["use_tta"])
    normal = meta["classes"].index(config.NORMAL_CLASS)
    correct = tp = fn = tn = fp = 0
    for row in rows:
        array = imaging.normalize(imaging.open_image(row["path"]))
        probs, _ = classifier.predict(array)
        predicted = meta["classes"][int(np.argmax(probs))]
        refer = (1.0 - probs[normal]) >= meta["referral_threshold"]
        abnormal = row["label"] != config.NORMAL_CLASS
        correct += predicted == row["label"]
        tp += abnormal and refer
        fn += abnormal and not refer
        tn += (not abnormal) and not refer
        fp += (not abnormal) and refer
    return {
        "cases": len(rows),
        "accuracy": correct / len(rows),
        "sensitivity": tp / (tp + fn) if tp + fn else None,
        "specificity": tn / (tn + fp) if tn + fp else None,
    }


def test_figures(evaluation):
    referral = evaluation.get("referral", {})
    return {
        "sensitivity": referral.get("sensitivity"),
        "specificity": referral.get("specificity"),
        "macro_f1": evaluation.get("test", {}).get("macro_f1"),
    }


def gate(current, candidate, local_cases):
    reasons, notes = [], []

    def check(name, cur, cand, tolerance, where):
        if cur is None or cand is None:
            return
        if cand < cur - tolerance:
            reasons.append("%s on the %s fell from %.1f%% to %.1f%%."
                           % (name, where, 100 * cur, 100 * cand))

    check("Sensitivity", current["test"]["sensitivity"], candidate["test"]["sensitivity"],
          TOLERANCE_TEST, "frozen test set")
    check("Macro F1", current["test"]["macro_f1"], candidate["test"]["macro_f1"],
          TOLERANCE_TEST, "frozen test set")
    if local_cases >= MIN_LOCAL_FOR_GATE:
        check("Accuracy", current["local"]["accuracy"], candidate["local"]["accuracy"],
              TOLERANCE_LOCAL, "held out local cases")
        check("Sensitivity", current["local"]["sensitivity"], candidate["local"]["sensitivity"],
              TOLERANCE_LOCAL, "held out local cases")
    else:
        notes.append("Only %d held out local cases, so the local comparison is shown but "
                     "not enforced." % local_cases)
    return {"passed": not reasons, "reasons": reasons + notes}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=int, required=True)
    args = parser.parse_args()

    app = make_db_app()
    with app.app_context():
        job_id = args.job_id
        try:
            set_job(job_id, message="Collecting reviewed cases.")
            rows, excluded = collect_reviewed()
            log("%d reviewed cases with a trained label" % len(rows))
            if excluded:
                log("excluded %d uploads identical to validation or test images" % excluded)
            if len(rows) < config.MIN_REVIEWS_FOR_RETRAIN:
                raise RuntimeError("Only %d usable reviewed cases; %d are required."
                                   % (len(rows), config.MIN_REVIEWS_FOR_RETRAIN))

            train_rows, holdout = split_by_patient(rows)
            log("%d cases for training, %d reserved from %d patients"
                % (len(train_rows), len(holdout), len({r["patient_id"] for r in holdout})))

            version = next_version()
            out_dir = os.path.join(CANDIDATES_DIR, version)
            os.makedirs(out_dir, exist_ok=True)
            extra = os.path.join(out_dir, "extra_rows.csv")
            with open(extra, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=["path", "label"])
                writer.writeheader()
                for row in train_rows:
                    writer.writerow({"path": row["path"], "label": row["label"]})

            current = ModelVersion.query.filter_by(status="active").first()
            target = config.read_json(config.absolute(current.calibration_path), {}).get(
                "target_sensitivity", 0.97)

            set_job(job_id, message="Training candidate %s." % version, reviews_used=len(train_rows))
            epochs = ["--epochs-head", "1", "--epochs-finetune", "1"] if QUICK else []
            model_path = os.path.join(out_dir, "model.h5")
            run_step([os.path.join("scripts", "train.py"), "--extra-rows", extra,
                      "--output", model_path,
                      "--summary", os.path.join(out_dir, "training_summary.json")] + epochs,
                     "training candidate %s" % version)

            set_job(job_id, message="Calibrating and testing candidate %s." % version)
            run_step([os.path.join("scripts", "calibrate.py"), "--model", model_path,
                      "--out-dir", out_dir, "--target-sensitivity", str(target)],
                     "calibrating candidate %s" % version)

            set_job(job_id, message="Comparing with the current model.")
            calibration = os.path.join(out_dir, "calibration.json")
            evaluation_path = os.path.join(out_dir, "evaluation_report.json")
            candidate_eval = config.read_json(evaluation_path, {})
            current_eval = config.read_json(config.absolute(current.evaluation_path), {})

            log("scoring current model %s on held out local cases" % current.version)
            current_local = evaluate_local(current.model_path, current.calibration_path, holdout)
            log("scoring candidate %s on held out local cases" % version)
            candidate_local = evaluate_local(model_path, calibration, holdout)

            comparison = {
                "current": {"version": current.version, "test": test_figures(current_eval),
                            "local": current_local},
                "candidate": {"version": version, "test": test_figures(candidate_eval),
                              "local": candidate_local},
                "local_cases": len(holdout),
            }
            decision = gate(comparison["current"], comparison["candidate"], len(holdout))
            log("gate %s" % ("passed" if decision["passed"] else "failed"))
            for reason in decision["reasons"]:
                log("  " + reason)

            summary = summarise(candidate_eval)
            summary.update({"comparison": comparison, "gate": decision,
                            "reviews_used": len(train_rows), "local_cases": len(holdout),
                            # The daily backtest leaves these out of this
                            # model's figures, so it is never marked on
                            # cases it learned from.
                            "trained_exam_ids": sorted(r["exam_id"] for r in train_rows),
                            "quick": QUICK})
            candidate = ModelVersion(
                version=version, status="candidate",
                model_path=config.relative(model_path),
                calibration_path=config.relative(calibration),
                evaluation_path=config.relative(evaluation_path),
                summary=summary,
                notes="Retrained with %d reviewed clinic cases%s." %
                      (len(train_rows), " (quick test run, one epoch)" if QUICK else ""),
            )
            db.session.add(candidate)
            db.session.flush()
            set_job(job_id, status="succeeded", finished_at=utcnow(), candidate_id=candidate.id,
                    message="Candidate %s is ready for review. It %s the validation gate."
                            % (version, "passed" if decision["passed"] else "did not pass"))
            log("done")
        except Exception as exc:
            traceback.print_exc()
            db.session.rollback()
            set_job(job_id, status="failed", finished_at=utcnow(), message=str(exc))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
