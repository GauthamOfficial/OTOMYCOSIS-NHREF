"""Build a training manifest from the Dataset folder.

Folder names are the source of truth for labels, but three folder pairs name
the same condition under different spellings and the chronic group mixes a
parent class with its own children. TAXONOMY below resolves that.

The split is made over visual clusters rather than individual files. Large
runs of near identical frames exist in several folders, and splitting on files
would put copies of the same ear in train and test, which inflates every
metric that follows.

Usage:
    python scripts/prepare_dataset.py
"""

import csv
import json
import os
import random
import sys
from collections import defaultdict

import numpy as np
from PIL import Image

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
DATASET_DIR = os.path.join(BASE_DIR, "Dataset")
OUT_DIR = os.path.join(BASE_DIR, "artifacts")

SEED = 1337
VAL_FRACTION = 0.15
TEST_FRACTION = 0.15
HASH_DISTANCE = 4          # bits of a 64 bit dHash, <= this counts as the same shot
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}

# Source folder -> training class.
# Folders sharing a value are merged because they name the same condition.
TAXONOMY = {
    "Acute otitis media":                                "acute_otitis_media",
    "normal":                                            "normal",
    "otitis externa":                                    "otitis_externa",
    "otitis media effusion":                             "otitis_media_effusion",

    # cerumen is ear wax, two spellings from two source datasets
    "Cerumen_Impaction":                                 "cerumen_impaction",
    "Ear wax":                                           "cerumen_impaction",

    # myringosclerosis and tympanosclerosis, same entity
    "Myringosclerosis":                                  "myringosclerosis",
    "tympanoskleros":                                    "myringosclerosis",

    # chronic group kept as flat leaves; the parent is recovered by summing
    # these probabilities at inference time (see PARENT_GROUPS)
    "Chronic_Otitis_Media":                              "chronic_om_unspecified",
    "Chronic Suppurative Otitis Media":                  "chronic_om_suppurative",
    "chronic active otitis media without cholesteatoma": "chronic_om_active_no_cholesteatoma",
    "chronic inactive otitis media":                     "chronic_om_inactive",
    "chronic otitis media with cholesteatoma":           "chronic_om_cholesteatoma",

    # too few images each to learn or validate alone, pooled as one class
    "Foreign Object Ear":                                "other_findings",
    "pseudoMembranes":                                   "other_findings",
    "Ear VentilationTube":                               "other_findings",
}

# Coarse groupings the API can report by summing member probabilities.
PARENT_GROUPS = {
    "chronic_otitis_media": [
        "chronic_om_unspecified",
        "chronic_om_suppurative",
        "chronic_om_active_no_cholesteatoma",
        "chronic_om_inactive",
        "chronic_om_cholesteatoma",
    ],
}


def dhash(path, size=8):
    """64 bit difference hash, or None if the file will not decode."""
    try:
        with Image.open(path) as im:
            grey = im.convert("L").resize((size + 1, size), Image.BILINEAR)
            pixels = np.asarray(grey, dtype=np.int16)
    except Exception:
        return None
    return np.packbits((pixels[:, 1:] > pixels[:, :-1]).flatten())


def cluster(hashes, max_distance=HASH_DISTANCE):
    """Group indices whose hashes are within max_distance of each other."""
    count = len(hashes)
    assigned = [-1] * count
    groups = 0
    for i in range(count):
        if assigned[i] != -1:
            continue
        assigned[i] = groups
        for j in range(i + 1, count):
            if assigned[j] == -1 and np.unpackbits(hashes[i] ^ hashes[j]).sum() <= max_distance:
                assigned[j] = groups
        groups += 1
    return assigned, groups


def split_clusters(sizes, rng):
    """Assign whole clusters to train/val/test, approximating the target ratios.

    Clusters are placed largest first into whichever split is furthest below
    its quota, so one big cluster cannot swamp a small split.
    """
    total = sum(sizes.values())
    quota = {
        "train": total * (1 - VAL_FRACTION - TEST_FRACTION),
        "val": total * VAL_FRACTION,
        "test": total * TEST_FRACTION,
    }
    filled = {"train": 0, "val": 0, "test": 0}
    placement = {}

    order = sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0]))
    for cluster_id, size in order:
        # Deficit relative to quota, scaled so the ratios are respected.
        choice = max(
            ("train", "val", "test"),
            key=lambda s: (quota[s] - filled[s]) / max(quota[s], 1e-9),
        )
        placement[cluster_id] = choice
        filled[choice] += size
    return placement


def main():
    if not os.path.isdir(DATASET_DIR):
        sys.exit("Dataset folder not found at %s" % DATASET_DIR)

    rng = random.Random(SEED)

    # Collect files per training class, after the taxonomy merge.
    per_class = defaultdict(list)
    unmapped = []
    for folder in sorted(os.listdir(DATASET_DIR)):
        folder_path = os.path.join(DATASET_DIR, folder)
        if not os.path.isdir(folder_path):
            continue
        label = TAXONOMY.get(folder)
        if label is None:
            unmapped.append(folder)
            continue
        for name in sorted(os.listdir(folder_path)):
            if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                per_class[label].append((folder, os.path.join(folder_path, name)))

    if unmapped:
        sys.exit("Folders missing from TAXONOMY: %s" % ", ".join(unmapped))

    rows = []
    print("%-38s %7s %9s %7s %6s %6s" % ("class", "files", "distinct", "train", "val", "test"))
    print("-" * 80)

    for label in sorted(per_class):
        entries = per_class[label]
        hashes, kept = [], []
        for folder, path in entries:
            h = dhash(path)
            if h is None:
                continue
            hashes.append(h)
            kept.append((folder, path))

        assigned, groups = cluster(hashes)

        sizes = defaultdict(int)
        for cid in assigned:
            sizes[cid] += 1
        placement = split_clusters(sizes, rng)

        tally = {"train": 0, "val": 0, "test": 0}
        for (folder, path), cid in zip(kept, assigned):
            split = placement[cid]
            tally[split] += 1
            rows.append({
                "path": os.path.relpath(path, BASE_DIR).replace("\\", "/"),
                "source_folder": folder,
                "label": label,
                "cluster": "%s#%d" % (label, cid),
                "split": split,
            })

        print("%-38s %7d %9d %7d %6d %6d" % (
            label, len(kept), groups, tally["train"], tally["val"], tally["test"]))

    classes = sorted(per_class)
    os.makedirs(OUT_DIR, exist_ok=True)

    manifest = os.path.join(OUT_DIR, "manifest.csv")
    with open(manifest, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["path", "source_folder", "label", "cluster", "split"])
        writer.writeheader()
        writer.writerows(rows)

    labels_path = os.path.join(OUT_DIR, "labels.json")
    with open(labels_path, "w", encoding="utf-8") as fh:
        json.dump({
            "classes": classes,
            "class_to_index": {c: i for i, c in enumerate(classes)},
            "parent_groups": PARENT_GROUPS,
            "taxonomy": TAXONOMY,
            "seed": SEED,
            "hash_distance": HASH_DISTANCE,
        }, fh, indent=2)

    totals = defaultdict(int)
    for row in rows:
        totals[row["split"]] += 1
    print("-" * 80)
    print("%-38s %7d %9s %7d %6d %6d" % (
        "TOTAL", len(rows), "", totals["train"], totals["val"], totals["test"]))
    print()
    print("classes  :", len(classes))
    print("manifest :", manifest)
    print("labels   :", labels_path)


if __name__ == "__main__":
    main()
