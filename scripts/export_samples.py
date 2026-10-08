"""Copy one held out test image per class into samples/ for demonstration.

Only test split images are used, so every sample is one the model never saw
during training or calibration. The folder is git ignored: the images belong
to the third party source datasets and are not redistributed with the code.

Usage:
    python scripts/export_samples.py [--per-class 1]
"""

import argparse
import csv
import os
import random
import shutil

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
MANIFEST = os.path.join(BASE_DIR, "artifacts", "manifest.csv")
OUT_DIR = os.path.join(BASE_DIR, "samples")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-class", type=int, default=1)
    args = parser.parse_args()

    with open(MANIFEST, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["split"] == "test"]

    by_class = {}
    for row in rows:
        by_class.setdefault(row["label"], []).append(row)

    os.makedirs(OUT_DIR, exist_ok=True)
    rng = random.Random(7)
    written = 0
    for label in sorted(by_class):
        # One image per visual cluster, so samples are not near copies.
        seen, choices = set(), []
        candidates = by_class[label][:]
        rng.shuffle(candidates)
        for row in candidates:
            if row["cluster"] not in seen:
                seen.add(row["cluster"])
                choices.append(row)
            if len(choices) == args.per_class:
                break
        for i, row in enumerate(choices, 1):
            source = os.path.join(BASE_DIR, row["path"])
            ext = os.path.splitext(source)[1].lower()
            target = os.path.join(OUT_DIR, "%s_%d%s" % (label, i, ext))
            if os.path.isfile(source):
                shutil.copyfile(source, target)
                written += 1

    print("wrote %d sample images to %s" % (written, OUT_DIR))


if __name__ == "__main__":
    main()
