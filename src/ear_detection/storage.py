"""Patient image storage.

Images live under STORAGE_DIR, outside the web root, and are only ever served
through an authenticated endpoint. Files are named by a random token, never by
the uploaded filename, which in clinics often contains the patient's name.

Each patient image is kept at no more than STORED_IMAGE_MAX_BYTES (200 KB by
default). An upload within the limit is kept byte for byte; a larger one is
re-encoded to fit before it is analysed, so the stored image is exactly the
one the result, the backtest and retraining are based on.
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


JPEG_QUALITIES = (92, 85, 78, 70)
SHRINK_STEP = 0.8


def fit_for_storage(raw_bytes, extension, image, limit=None):
    """The bytes to keep for an image, at most ``limit`` bytes.

    An image within the limit is kept exactly as received. A larger one is
    re-encoded as JPEG at full resolution with gradually lower quality, and
    only if that is not enough at gradually smaller sizes, stopping at the
    first version that fits. Detail matters more than file size, so
    resolution is the last thing given up. Re-encoding also drops embedded
    metadata such as camera details.

    ``image`` is the upright RGB image decoded from ``raw_bytes``. Returns the
    bytes, their file extension and the image they decode to.
    """
    limit = limit or config.STORED_IMAGE_MAX_BYTES
    if len(raw_bytes) <= limit:
        return raw_bytes, extension, image

    width, height = image.size
    scale = 1.0
    while True:
        size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
        candidate = image if scale == 1.0 else image.resize(size, Image.LANCZOS)
        for quality in JPEG_QUALITIES:
            buffer = io.BytesIO()
            candidate.save(buffer, format="JPEG", quality=quality, optimize=True)
            data = buffer.getvalue()
            if len(data) <= limit:
                return data, ".jpg", Image.open(io.BytesIO(data)).convert("RGB")
        scale *= SHRINK_STEP


def save_examination(raw_bytes, extension, model_view, overlay, upload_sha256):
    """Write the stored image and its derived images.

    ``upload_sha256`` is the hash of the file as uploaded, before any
    re-encoding, so duplicate uploads and copies of dataset images are still
    recognised. Returns the stored relative paths and that hash.
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

    paths["image_sha256"] = upload_sha256
    return paths


def sha256(data):
    return hashlib.sha256(data).hexdigest()


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
