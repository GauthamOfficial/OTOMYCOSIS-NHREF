"""Configuration for the ear condition screening service."""

import json
import os

# Repository root, resolved relative to this file so the service works
# regardless of the directory it is started from.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))

FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
ARTIFACTS_DIR = os.path.join(BASE_DIR, "artifacts")

MODEL_PATH = os.environ.get("MODEL_PATH", os.path.join(BASE_DIR, "models", "ear_classifier.keras"))
LABELS_FILE = os.path.join(ARTIFACTS_DIR, "labels.json")
CALIBRATION_FILE = os.path.join(ARTIFACTS_DIR, "calibration.json")
INPUT_STATS_FILE = os.path.join(ARTIFACTS_DIR, "input_stats.json")
EVALUATION_FILE = os.path.join(ARTIFACTS_DIR, "evaluation_report.json")

API_PREFIX = "/api/v1"

# Uploads are read into memory, analysed and discarded. Nothing derived from a
# patient image is written to disk.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
MIN_IMAGE_SIDE = 128

NORMAL_CLASS = "normal"

# Readable names and one line descriptions, shown in the interface and in the
# validation report. Keys are the training class names in artifacts/labels.json.
CLASS_INFO = {
    "acute_otitis_media": (
        "Acute Otitis Media",
        "Acute middle ear infection; bulging, erythematous tympanic membrane.",
    ),
    "cerumen_impaction": (
        "Cerumen Impaction",
        "Ear wax occluding the canal or obscuring the tympanic membrane.",
    ),
    "chronic_om_active_no_cholesteatoma": (
        "Chronic OM, Active, No Cholesteatoma",
        "Perforation with active discharge and no cholesteatoma.",
    ),
    "chronic_om_cholesteatoma": (
        "Chronic OM with Cholesteatoma",
        "Keratinising squamous epithelium in the middle ear; erosive.",
    ),
    "chronic_om_inactive": (
        "Chronic OM, Inactive",
        "Dry tympanic membrane perforation without discharge.",
    ),
    "chronic_om_suppurative": (
        "Chronic Suppurative OM",
        "Persistent perforation with chronic discharge.",
    ),
    "chronic_om_unspecified": (
        "Chronic Otitis Media",
        "Chronic middle ear disease; subtype not recorded in the source data.",
    ),
    "myringosclerosis": (
        "Myringosclerosis",
        "Calcified white plaques in the tympanic membrane; includes tympanosclerosis.",
    ),
    "normal": (
        "Normal Ear",
        "No abnormality detected.",
    ),
    "other_findings": (
        "Other Findings",
        "Foreign body, pseudomembrane or ventilation tube, pooled for lack of images.",
    ),
    "otitis_externa": (
        "Otitis Externa",
        "Inflammation of the external ear canal.",
    ),
    "otitis_media_effusion": (
        "Otitis Media with Effusion",
        "Fluid behind an intact tympanic membrane without acute infection.",
    ),
}

PARENT_DISPLAY_NAMES = {
    "chronic_otitis_media": "Chronic Otitis Media, any subtype",
}

# A softmax vector can peak on a class without the network having decided
# anything, so the leading finding is flagged as low confidence when the gap
# to the runner up is small or the distribution is close to uniform. This
# affects how the finding is presented, never the referral decision, which
# has its own threshold below.
CONFIDENCE_MIN_MARGIN = 0.15
CONFIDENCE_MAX_ENTROPY_RATIO = 0.80

# Fallbacks used only if scripts/calibrate.py has not been run.
DEFAULT_TEMPERATURE = 1.0
DEFAULT_REFERRAL_THRESHOLD = 0.5


def _read_json(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_runtime_metadata():
    """Everything the service needs to know about the trained model.

    The class order is read from the file written when the dataset was
    prepared. It cannot be inferred from the weights, and losing it is what
    made the project's original model unusable.
    """
    labels = _read_json(LABELS_FILE)
    if labels is None:
        raise RuntimeError(
            "artifacts/labels.json is missing. Run scripts/prepare_dataset.py "
            "and scripts/train.py before starting the service."
        )

    calibration = _read_json(CALIBRATION_FILE, default={})
    return {
        "classes": labels["classes"],
        "parent_groups": labels.get("parent_groups", {}),
        "temperature": calibration.get("temperature", DEFAULT_TEMPERATURE),
        "referral_threshold": calibration.get("referral_threshold", DEFAULT_REFERRAL_THRESHOLD),
        "use_tta": calibration.get("use_tta", True),
        "calibrated": bool(calibration),
        "calibration": calibration,
        "input_stats": _read_json(INPUT_STATS_FILE, default={}),
        "evaluation": _read_json(EVALUATION_FILE, default={}),
    }
