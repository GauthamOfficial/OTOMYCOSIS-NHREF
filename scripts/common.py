"""Paths and helpers shared by the training pipeline scripts."""

import hashlib
import os
import re
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

ARTIFACTS = os.path.join(BASE_DIR, "artifacts")
DATASET_DIR = os.path.join(BASE_DIR, "Dataset")
MANIFEST = os.path.join(ARTIFACTS, "manifest.csv")
LABELS = os.path.join(ARTIFACTS, "labels.json")
NORMALIZED_DIR = os.path.join(ARTIFACTS, "normalized")


def normalized_path(relative_path):
    """Where the field of view normalised copy of an image is cached."""
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()
    return os.path.join(NORMALIZED_DIR, digest + ".png")


def source_of(relative_path):
    """Which of the three merged source datasets an image came from.

    Identified by filename convention: A uses ``AOM_000.jpg``, C uses
    ``Processed_1.jpg``, B uses ``name (1).png``.
    """
    name = relative_path.replace("\\", "/").rsplit("/", 1)[-1]
    if re.match(r"^[A-Z]{2,4}_\d+\.", name):
        return "A"
    if name.startswith("Processed_"):
        return "C"
    return "B"
