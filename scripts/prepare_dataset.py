"""Build the training manifest from the Dataset folder.

Three steps, each guarding against a way the evaluation could flatter itself.

1. Taxonomy. Folder names are the labels, but three folder pairs name the
   same condition under different spellings and the chronic group mixes a
   parent class with its children. TAXONOMY resolves both.

2. Field of view normalisation. Every image is passed through
   ear_detection.imaging.normalize, the function the service uses at
   inference, and cached. See imaging.py for why.

3. Same ear grouping. One source dataset is built from video, so the same
   ear appears in long runs of frames. A perceptual hash only catches frames
   that are nearly identical; as the otoscope moves, frames of the same ear
   drift past it, and a split made on hash clusters left half of that
   source's test images with a near twin in training. Images are therefore
   grouped by the cosine similarity of generic ImageNet features, joined
   transitively, and whole groups are assigned to one split. At the chosen
   threshold no group exceeds a tenth of its class, so the grouping does not
   chain distinct ears together.

Usage:
    python scripts/prepare_dataset.py
"""

import csv
import json
import os
import sys
import time
from collections import Counter

import numpy as np
from PIL import Image

from common import (
    ARTIFACTS, BASE_DIR, DATASET_DIR, LABELS, MANIFEST, NORMALIZED_DIR,
    normalized_path, source_of,
)
from ear_detection import imaging

SEED = 1337
VAL_FRACTION = 0.15
TEST_FRACTION = 0.15

# Cosine similarity of ImageNet MobileNetV2 features at or above which two
# images of the same class are treated as the same ear. Chosen as the lowest
# value tested whose largest group stays under 10 percent of every class.
GROUP_SIMILARITY = 0.93

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}

# Source folder -> training class. Folders sharing a value are merged because
# they name the same condition.
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
    # these probabilities (see PARENT_GROUPS)
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

PARENT_GROUPS = {
    "chronic_otitis_media": [
        "chronic_om_unspecified",
        "chronic_om_suppurative",
        "chronic_om_active_no_cholesteatoma",
        "chronic_om_inactive",
        "chronic_om_cholesteatoma",
    ],
}


def collect():
    entries, unmapped = [], []
    for folder in sorted(os.listdir(DATASET_DIR)):
        path = os.path.join(DATASET_DIR, folder)
        if not os.path.isdir(path):
            continue
        label = TAXONOMY.get(folder)
        if label is None:
            unmapped.append(folder)
            continue
        for name in sorted(os.listdir(path)):
            if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                rel = os.path.relpath(os.path.join(path, name), BASE_DIR).replace("\\", "/")
                entries.append({"path": rel, "source_folder": folder, "label": label})
    if unmapped:
        sys.exit("Folders missing from TAXONOMY: %s" % ", ".join(unmapped))
    return entries


def normalise_all(entries):
    os.makedirs(NORMALIZED_DIR, exist_ok=True)
    kept, started = [], time.time()
    for i, entry in enumerate(entries):
        target = normalized_path(entry["path"])
        if not os.path.isfile(target):
            try:
                array = imaging.normalize(imaging.open_image(os.path.join(BASE_DIR, entry["path"])))
            except Exception as exc:  # unreadable file: report and skip
                print("  skipping unreadable %s (%s)" % (entry["path"], exc))
                continue
            Image.fromarray(array).save(target)
        kept.append(entry)
        if (i + 1) % 1000 == 0:
            print("  normalised %d / %d  (%.0fs)" % (i + 1, len(entries), time.time() - started), flush=True)
    return kept


def embed(entries):
    """L2 normalised ImageNet features of each normalised image."""
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    from tensorflow import keras

    model = keras.applications.MobileNetV2(
        include_top=False, weights="imagenet", pooling="avg",
        input_shape=(imaging.INPUT_SIZE, imaging.INPUT_SIZE, 3),
    )
    features = np.zeros((len(entries), model.output_shape[-1]), np.float32)
    for start in range(0, len(entries), 32):
        chunk = entries[start:start + 32]
        batch = np.stack([
            np.asarray(Image.open(normalized_path(e["path"])).convert("RGB"), np.float32)
            for e in chunk
        ])
        out = model(keras.applications.mobilenet_v2.preprocess_input(batch), training=False)
        features[start:start + len(chunk)] = out.numpy()
    features /= np.linalg.norm(features, axis=1, keepdims=True)
    return features


def group(indices, features, threshold):
    """Connected components of the similarity graph within one class."""
    sub = features[indices]
    similar = np.triu(sub @ sub.T >= threshold, 1)
    parent = list(range(len(indices)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in zip(*np.where(similar)):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return [find(i) for i in range(len(indices))]


def split_groups(sizes):
    """Assign whole groups to train, val and test, largest first.

    Each group goes to whichever split is furthest below its quota, so one
    large group cannot swamp a small split.
    """
    total = sum(sizes.values())
    quota = {
        "train": total * (1 - VAL_FRACTION - TEST_FRACTION),
        "val": total * VAL_FRACTION,
        "test": total * TEST_FRACTION,
    }
    filled = {s: 0 for s in quota}
    placement = {}
    for gid, size in sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0])):
        choice = max(quota, key=lambda s: (quota[s] - filled[s]) / quota[s])
        placement[gid] = choice
        filled[choice] += size
    return placement


def main():
    if not os.path.isdir(DATASET_DIR):
        sys.exit("Dataset folder not found at %s" % DATASET_DIR)

    print("collecting", flush=True)
    entries = collect()
    print("normalising %d images" % len(entries), flush=True)
    entries = normalise_all(entries)
    print("embedding", flush=True)
    features = embed(entries)

    labels = np.array([e["label"] for e in entries])
    classes = sorted(set(labels))

    print("\n%-38s %6s %7s %7s %6s %6s" % ("class", "files", "groups", "train", "val", "test"))
    print("-" * 76)
    for label in classes:
        idx = np.where(labels == label)[0]
        roots = group(idx, features, GROUP_SIMILARITY)
        sizes = Counter(roots)
        placement = split_groups(sizes)
        tally = Counter()
        for i, root in zip(idx, roots):
            entries[i]["cluster"] = "%s#%d" % (label, root)
            entries[i]["split"] = placement[root]
            tally[placement[root]] += 1
        print("%-38s %6d %7d %7d %6d %6d" % (
            label, len(idx), len(sizes), tally["train"], tally["val"], tally["test"]))

    # Verify the guarantee the grouping exists to provide.
    split = np.array([e["split"] for e in entries])
    worst = 0.0
    for i in np.where(split == "test")[0]:
        train_same = np.where((split == "train") & (labels == labels[i]))[0]
        worst = max(worst, float((features[train_same] @ features[i]).max()))
    print("-" * 76)
    print("most similar same class train image to any test image: cosine %.4f"
          " (must be below %.2f)" % (worst, GROUP_SIMILARITY))
    if worst >= GROUP_SIMILARITY:
        sys.exit("grouping failed to separate test from train")

    os.makedirs(ARTIFACTS, exist_ok=True)
    with open(MANIFEST, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["path", "source_folder", "source", "label", "cluster", "split"])
        writer.writeheader()
        for e in entries:
            writer.writerow({**e, "source": source_of(e["path"])})

    with open(LABELS, "w", encoding="utf-8") as fh:
        json.dump({
            "classes": classes,
            "class_to_index": {c: i for i, c in enumerate(classes)},
            "parent_groups": PARENT_GROUPS,
            "taxonomy": TAXONOMY,
            "seed": SEED,
            "grouping": {
                "method": "connected components of ImageNet MobileNetV2 feature cosine similarity",
                "threshold": GROUP_SIMILARITY,
                "max_test_to_train_similarity": worst,
            },
        }, fh, indent=2)

    print("\nsplits:", dict(Counter(split)))
    print("manifest:", MANIFEST)
    print("labels:  ", LABELS)


if __name__ == "__main__":
    main()
