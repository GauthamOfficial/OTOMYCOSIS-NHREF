"""Configuration for the ear disease detection service."""

import os

# Repository root, resolved relative to this file so the service works
# regardless of the directory it is started from.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))

MODEL_PATH = os.environ.get("MODEL_PATH", os.path.join(BASE_DIR, "models", "my_model_44.h5"))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))

# Input size expected by the trained network.
IMAGE_SIZE = (224, 224)

# Class index -> human readable label, in the order the model was trained on.
CLASS_LABELS = {
    0: "AOM",
    1: "CSOM",
    2: "Wax",
    3: "Glue Ear",
    4: "Normal Ear",
    5: "Others",
    6: "Otomycosis",
}

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}

API_PREFIX = "/api/v0.1"
