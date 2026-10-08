"""Configuration for the ear disease detection service."""

import json
import os

# Repository root, resolved relative to this file so the service works
# regardless of the directory it is started from.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))

FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
ARTIFACTS_DIR = os.path.join(BASE_DIR, "artifacts")

TRAINED_MODEL = os.path.join(BASE_DIR, "models", "ear_classifier.keras")
LEGACY_MODEL = os.path.join(BASE_DIR, "models", "my_model_44.h5")
LABELS_FILE = os.path.join(ARTIFACTS_DIR, "labels.json")

# Input size expected by the network.
IMAGE_SIZE = (224, 224)

# Readable names for the classes produced by scripts/prepare_dataset.py.
DISPLAY_NAMES = {
    "acute_otitis_media": "Acute Otitis Media",
    "cerumen_impaction": "Cerumen Impaction",
    "chronic_om_active_no_cholesteatoma": "Chronic OM, Active, No Cholesteatoma",
    "chronic_om_cholesteatoma": "Chronic OM with Cholesteatoma",
    "chronic_om_inactive": "Chronic OM, Inactive",
    "chronic_om_suppurative": "Chronic Suppurative OM",
    "chronic_om_unspecified": "Chronic Otitis Media",
    "myringosclerosis": "Myringosclerosis",
    "normal": "Normal Ear",
    "other_findings": "Other Findings",
    "otitis_externa": "Otitis Externa",
    "otitis_media_effusion": "Otitis Media with Effusion",
}

# Labels the previous weights were assumed to produce. The notebook that
# trained them was never committed, so this order was never verifiable. It is
# kept only so the legacy file still runs, and it is not used once the
# retrained model is present.
LEGACY_CLASS_LABELS = {
    0: "AOM",
    1: "CSOM",
    2: "Wax",
    3: "Glue Ear",
    4: "Normal Ear",
    5: "Others",
    6: "Otomycosis",
}


def _load_label_metadata():
    """Read the class order recorded at training time.

    The order a model was trained in cannot be inferred from its weights, so
    it is written to artifacts/labels.json during preparation and read back
    here. Falling back to the legacy map keeps the old weights usable.
    """
    if os.path.isfile(TRAINED_MODEL) and os.path.isfile(LABELS_FILE):
        with open(LABELS_FILE, encoding="utf-8") as fh:
            meta = json.load(fh)
        classes = meta["classes"]
        return {
            "model_path": TRAINED_MODEL,
            "labels": {i: DISPLAY_NAMES.get(name, name) for i, name in enumerate(classes)},
            "raw_classes": classes,
            "parent_groups": meta.get("parent_groups", {}),
            "verified": True,
        }

    return {
        "model_path": LEGACY_MODEL,
        "labels": dict(LEGACY_CLASS_LABELS),
        "raw_classes": [LEGACY_CLASS_LABELS[i] for i in sorted(LEGACY_CLASS_LABELS)],
        "parent_groups": {},
        "verified": False,
    }


_METADATA = _load_label_metadata()

MODEL_PATH = os.environ.get("MODEL_PATH", _METADATA["model_path"])
CLASS_LABELS = _METADATA["labels"]
RAW_CLASSES = _METADATA["raw_classes"]
PARENT_GROUPS = _METADATA["parent_groups"]

# False means the running weights are the legacy ones whose class order was
# never recorded, so the names attached to predictions are unconfirmed.
LABELS_VERIFIED = _METADATA["verified"]

# Readable names for the coarse groups, used when the differential is rolled up.
PARENT_DISPLAY_NAMES = {
    "chronic_otitis_media": "Chronic Otitis Media (any subtype)",
}

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}

API_PREFIX = "/api/v0.1"

# A softmax vector can peak on a class without the network having made a real
# decision, so each result is checked on two signals: the gap between the top
# two probabilities, and entropy as a fraction of its maximum. Failing either
# marks the result inconclusive rather than presenting it as a finding.
CONFIDENCE_MIN_MARGIN = 0.15
CONFIDENCE_MAX_ENTROPY_RATIO = 0.80
