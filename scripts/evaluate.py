"""Evaluate the trained classifier on the held out test split.

Reports per class precision, recall and F1, the macro average, and a confusion
matrix. Accuracy is printed but should not be the headline: the test split runs
from 217 images for normal down to 5 for other_findings, so a majority class
predictor would post a respectable accuracy while being useless.

Also reports coarse accuracy over the parent groups in labels.json, where the
five chronic classes collapse into one. If the fine classes confuse only with
their own siblings while the coarse number stays high, the subtypes are the
limitation rather than the model.

Usage:
    python scripts/evaluate.py [--split test]
"""

import argparse
import csv
import json
import os

import numpy as np
import tensorflow as tf
from tensorflow import keras

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
ARTIFACTS = os.path.join(BASE_DIR, "artifacts")
MODEL_PATH = os.path.join(BASE_DIR, "models", "ear_classifier.keras")
IMAGE_SIZE = (224, 224)
BATCH_SIZE = 16


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="test")
    args = parser.parse_args()

    with open(os.path.join(ARTIFACTS, "labels.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    classes = meta["classes"]
    index = meta["class_to_index"]

    with open(os.path.join(ARTIFACTS, "manifest.csv"), newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["split"] == args.split]

    paths = [os.path.join(BASE_DIR, r["path"]) for r in rows]
    truth = np.array([index[r["label"]] for r in rows])

    def decode(path):
        image = tf.io.decode_image(tf.io.read_file(path), channels=3, expand_animations=False)
        image = tf.image.resize(image, IMAGE_SIZE)
        image.set_shape([IMAGE_SIZE[0], IMAGE_SIZE[1], 3])
        return keras.applications.mobilenet_v2.preprocess_input(image)

    ds = (tf.data.Dataset.from_tensor_slices(paths)
          .map(decode, num_parallel_calls=tf.data.AUTOTUNE)
          .batch(BATCH_SIZE)
          .prefetch(tf.data.AUTOTUNE))

    model = keras.models.load_model(MODEL_PATH)
    probs = model.predict(ds, verbose=0)
    pred = np.argmax(probs, axis=1)

    n = len(classes)
    print("Split: %s   images: %d   classes: %d\n" % (args.split, len(rows), n))
    print("%-38s %6s %7s %7s %7s" % ("class", "n", "prec", "recall", "f1"))
    print("-" * 70)

    f1s = []
    for c in range(n):
        tp = int(np.sum((pred == c) & (truth == c)))
        fp = int(np.sum((pred == c) & (truth != c)))
        fn = int(np.sum((pred != c) & (truth == c)))
        support = int(np.sum(truth == c))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1s.append(f1)
        print("%-38s %6d %7.3f %7.3f %7.3f" % (classes[c], support, precision, recall, f1))

    accuracy = float(np.mean(pred == truth))
    macro_f1 = float(np.mean(f1s))
    print("-" * 70)
    print("%-38s %6d %7s %7s %7.3f" % ("MACRO AVERAGE", len(rows), "", "", macro_f1))
    print("%-38s %6s %7s %7s %7.3f" % ("accuracy (majority biased)", "", "", "", accuracy))

    # Coarse view: collapse each parent group into a single label.
    coarse_of = {}
    for parent, members in meta.get("parent_groups", {}).items():
        for m in members:
            coarse_of[m] = parent
    coarse_name = [coarse_of.get(c, c) for c in classes]
    coarse_ids = {name: i for i, name in enumerate(sorted(set(coarse_name)))}
    ct = np.array([coarse_ids[coarse_name[t]] for t in truth])
    cp = np.array([coarse_ids[coarse_name[p]] for p in pred])
    print("%-38s %6s %7s %7s %7.3f" % ("coarse accuracy (groups merged)", "", "", "", float(np.mean(ct == cp))))

    print("\nConfusion matrix (rows = truth, columns = prediction)")
    width = max(len(c) for c in classes)
    header = " " * (width + 2) + "".join("%5d" % i for i in range(n))
    print(header)
    for c in range(n):
        line = "%-*s  " % (width, classes[c])
        for p in range(n):
            count = int(np.sum((truth == c) & (pred == p)))
            line += "%5s" % (count if count else ".")
        print(line + "   [%d]" % c)

    report = {
        "split": args.split,
        "classes": classes,
        "macro_f1": macro_f1,
        "accuracy": accuracy,
        "per_class_f1": {classes[c]: f1s[c] for c in range(n)},
        "support": {classes[c]: int(np.sum(truth == c)) for c in range(n)},
    }
    out = os.path.join(ARTIFACTS, "evaluation_%s.json" % args.split)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print("\nreport written to", out)


if __name__ == "__main__":
    main()
