"""Patient image storage.

Images live under STORAGE_DIR, outside the web root, and are only ever served
through an authenticated endpoint. Files are named by a random token, never by
the uploaded filename, which in clinics often contains the patient's name.
The original upload is kept byte for byte so it can be used for retraining.
"""

import hashlib
import io
import os
import uuid
from datetime import datetime, timezone

from PIL import Image

from . import config


def absolute(relative_path):
    """Resolve a stored path, refusing anything that escapes STORAGE_DIR."""
    root = os.path.realpath(config.STORAGE_DIR)
    full = os.path.realpath(os.path.join(root, relative_path))
    if os.path.commonpath([root, full]) != root:
        raise ValueError("path escapes storage directory")
    return full


def _write(relative_path, data):
    full = absolute(relative_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data)


def _png(array):
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


def save_examination(raw_bytes, extension, model_view, overlay):
    """Write the original upload and its derived images.

    Returns the stored relative paths and the SHA 256 of the original.
    """
    now = datetime.now(timezone.utc)
    folder = "%04d/%02d" % (now.year, now.month)
    token = uuid.uuid4().hex

    paths = {
        "image_path": "images/%s/%s%s" % (folder, token, extension),
        "model_view_path": "derived/%s/%s_view.png" % (folder, token),
        "overlay_path": "derived/%s/%s_overlay.png" % (folder, token),
    }
    try:
        _write(paths["image_path"], raw_bytes)
        _write(paths["model_view_path"], _png(model_view))
        _write(paths["overlay_path"], _png(overlay))
    except Exception:
        remove(paths.values())
        raise

    paths["image_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    return paths


def remove(relative_paths):
    """Best effort cleanup, used when a database write fails."""
    for path in relative_paths:
        try:
            os.remove(absolute(path))
        except (OSError, ValueError, TypeError):
            pass


MIME_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff",
    ".webp": "image/webp",
}


def mime_type(relative_path):
    return MIME_TYPES.get(os.path.splitext(relative_path)[1].lower(), "application/octet-stream")
