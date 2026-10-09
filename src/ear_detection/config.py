"""Configuration for the ear condition screening service.

Secrets and deployment settings come from environment variables, normally set
in a ``.env`` file at the repository root (see ``.env.example``). Run
``python scripts/manage.py setup`` to create one.
"""

import json
import os

from dotenv import load_dotenv

# Repository root, resolved relative to this file so the service works
# regardless of the directory it is started from.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))

load_dotenv(os.path.join(BASE_DIR, ".env"))


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
ARTIFACTS_DIR = os.path.join(BASE_DIR, "artifacts")

# The weights and calibration shipped with the repository. They are registered
# as the first model version when the database is created; later versions are
# produced by retraining and live under models/candidates/.
DEFAULT_MODEL_PATH = os.path.join(BASE_DIR, "models", "ear_classifier.keras")
LABELS_FILE = os.path.join(ARTIFACTS_DIR, "labels.json")
CALIBRATION_FILE = os.path.join(ARTIFACTS_DIR, "calibration.json")
INPUT_STATS_FILE = os.path.join(ARTIFACTS_DIR, "input_stats.json")
EVALUATION_FILE = os.path.join(ARTIFACTS_DIR, "evaluation_report.json")
DEFAULT_MODEL_VERSION = "2.0.0"

API_PREFIX = "/api/v1"

# ---------------- deployment ----------------

# PostgreSQL in production, for example
#   postgresql+psycopg://ear_app:secret@localhost:5432/ear_screening
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Signs every login token. Must be long and random; never commit it.
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "")

# Where patient images are kept. Outside the web root, never served directly.
STORAGE_DIR = os.environ.get("STORAGE_DIR") or os.path.join(BASE_DIR, "storage")

# Set true once the service is behind HTTPS, so login cookies are never sent
# over plain HTTP.
COOKIE_SECURE = _env_bool("COOKIE_SECURE", False)

# Set true only when a reverse proxy in front of the service sets X-Real-IP.
# Otherwise that header is client supplied and could falsify the audit log.
TRUST_PROXY = _env_bool("TRUST_PROXY", False)

# ---------------- Google sign in ----------------

# The OAuth client ID from the Google Cloud Console. When it is set, people
# sign in with their Google account and only administrators may still use a
# password, so the system stays reachable if Google cannot be. Empty means
# password sign in for everyone, as before.
GOOGLE_CLIENT_ID = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
# Roles that may still sign in with a password once Google is configured.
PASSWORD_FALLBACK_ROLES = ("admin",)


def google_enabled():
    return bool(GOOGLE_CLIENT_ID)


def env_problems(path=None):
    """Lines of .env that are not settings and were therefore ignored.

    A setting pasted in as ``Client id = ...`` looks right to a person and is
    invisible to the program, which then behaves as though it were never set.
    Values are never included here, since the file holds secrets.
    """
    import re

    path = path or os.path.join(BASE_DIR, ".env")
    problems = []
    if not os.path.isfile(path):
        return problems
    with open(path, encoding="utf-8", errors="replace") as fh:
        for number, line in enumerate(fh, 1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            name = text.partition("=")[0].strip()
            if "=" not in text:
                problems.append("Line %d of .env is not a setting and was ignored: %r"
                                % (number, text[:30]))
            elif not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                problems.append("Line %d of .env was ignored: %r is not a setting name. Use "
                                "NAME=value with no spaces around the equals sign."
                                % (number, name[:30]))
    return problems


ACCESS_TOKEN_MINUTES = int(os.environ.get("ACCESS_TOKEN_MINUTES") or 15)
REFRESH_TOKEN_HOURS = int(os.environ.get("REFRESH_TOKEN_HOURS") or 10)

LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_MINUTES = 15
MIN_PASSWORD_LENGTH = 10

# Reviewed cases needed before a retraining run is allowed.
MIN_REVIEWS_FOR_RETRAIN = int(os.environ.get("MIN_REVIEWS_FOR_RETRAIN") or 50)

# ---------------- daily backtest ----------------

# Once a day the model in service is re-run on every surgeon reviewed case and
# compared with the surgeons' verdicts. It runs at or after this local hour,
# or shortly after start up if that day's run was missed.
BACKTEST_ENABLED = _env_bool("BACKTEST_ENABLED", True)
BACKTEST_HOUR = int(os.environ.get("BACKTEST_HOUR") or 2)
# Recent window reported alongside all time figures, so a recent decline is
# not hidden by older good results.
BACKTEST_WINDOW_DAYS = int(os.environ.get("BACKTEST_WINDOW_DAYS") or 30)
# Alert when recent referral sensitivity falls below this...
BACKTEST_MIN_SENSITIVITY = float(os.environ.get("BACKTEST_MIN_SENSITIVITY") or 0.90)
# ...judged only once this many abnormal reviewed cases exist in the window.
BACKTEST_MIN_CASES = int(os.environ.get("BACKTEST_MIN_CASES") or 30)
# Alert when recent agreement is this far below the long term figure.
BACKTEST_MAX_AGREEMENT_DROP = float(os.environ.get("BACKTEST_MAX_AGREEMENT_DROP") or 0.10)

# ---------------- uploads ----------------

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
# Largest file kept for a patient image. Larger uploads are re-encoded as JPEG
# to fit (see storage.fit_for_storage). Never below 20 KB, which would force
# images too small to be useful.
STORED_IMAGE_MAX_BYTES = max(20, int(os.environ.get("STORED_IMAGE_MAX_KB") or 200)) * 1024
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
MIN_IMAGE_SIDE = 128

SEX_CHOICES = ("female", "male", "other")
EAR_CHOICES = ("left", "right")
ROLE_CHOICES = ("admin", "clinician")

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
# has its own threshold.
CONFIDENCE_MIN_MARGIN = 0.15
CONFIDENCE_MAX_ENTROPY_RATIO = 0.80

DEFAULT_TEMPERATURE = 1.0
DEFAULT_REFERRAL_THRESHOLD = 0.5


def display_name(name):
    return CLASS_INFO.get(name, (name, ""))[0]


def description(name):
    return CLASS_INFO.get(name, (name, ""))[1]


def read_json(path, default=None):
    if not path or not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def absolute(path):
    """Resolve a path stored relative to the repository root."""
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


def relative(path):
    """Store paths relative to the repository root so the folder can move."""
    try:
        return os.path.relpath(path, BASE_DIR).replace("\\", "/")
    except ValueError:  # different drive on Windows
        return path


def load_labels():
    """Class order recorded when the dataset was prepared.

    It cannot be inferred from the weights, and losing it is what made the
    project's original model unusable.
    """
    labels = read_json(LABELS_FILE)
    if labels is None:
        raise RuntimeError(
            "artifacts/labels.json is missing. Run scripts/prepare_dataset.py "
            "and scripts/train.py before starting the service."
        )
    return labels


def model_metadata(model_path, calibration_path=None, evaluation_path=None):
    """Everything inference needs about one trained model."""
    labels = load_labels()
    calibration = read_json(absolute(calibration_path), default={}) or {}
    return {
        "model_path": absolute(model_path),
        "classes": labels["classes"],
        "parent_groups": labels.get("parent_groups", {}),
        "temperature": calibration.get("temperature", DEFAULT_TEMPERATURE),
        "referral_threshold": calibration.get("referral_threshold", DEFAULT_REFERRAL_THRESHOLD),
        "use_tta": calibration.get("use_tta", True),
        "calibrated": bool(calibration),
        "calibration": calibration,
        "evaluation": read_json(absolute(evaluation_path), default={}) or {},
        "input_stats": read_json(INPUT_STATS_FILE, default={}) or {},
    }
