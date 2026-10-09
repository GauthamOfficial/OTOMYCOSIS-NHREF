"""Stored patient images never exceed the size limit."""

import argparse
import io
import os
import sys

import numpy as np
from PIL import Image

from conftest import ROOT, SAMPLE, examination

LIMIT = 200 * 1024


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def large_photo(size=(2400, 1800)):
    """A smooth, otoscope like image whose PNG is several megabytes."""
    return Image.open(SAMPLE).convert("RGB").resize(size, Image.BICUBIC)


def test_the_default_limit_is_200_kb():
    from ear_detection import config

    assert config.STORED_IMAGE_MAX_BYTES == LIMIT


def test_images_within_the_limit_are_kept_exactly():
    from ear_detection import imaging, storage

    with open(SAMPLE, "rb") as fh:
        raw = fh.read()
    assert len(raw) <= LIMIT
    data, extension, _ = storage.fit_for_storage(raw, ".jpg", imaging.open_image(raw))
    assert data is raw and extension == ".jpg"


def test_large_images_keep_full_resolution_when_quality_is_enough():
    from ear_detection import storage

    image = large_photo()
    raw = png(image)
    assert len(raw) > 3 * LIMIT
    data, extension, stored = storage.fit_for_storage(raw, ".png", image)
    assert len(data) <= LIMIT
    assert extension == ".jpg"
    assert stored.size == image.size
    assert Image.open(io.BytesIO(data)).format == "JPEG"


def test_images_that_cannot_compress_are_reduced_until_they_fit():
    from ear_detection import storage

    noise = Image.fromarray(np.random.default_rng(0).integers(0, 256, (1500, 2000, 3), dtype=np.uint8))
    data, _, stored = storage.fit_for_storage(png(noise), ".png", noise)
    assert len(data) <= LIMIT
    assert stored.size[0] < 2000
    assert abs(stored.size[0] / stored.size[1] - 2000 / 1500) < 0.01


def test_uploads_are_stored_within_the_limit(app, client, clinician):
    from ear_detection import storage
    from ear_detection.db import Examination, db

    raw = png(large_photo())
    response = examination(client, clinician, record_no="NHR-SIZE-1", data=raw, filename="big.png")
    assert response.status_code == 201
    exam = response.get_json()
    assert exam["image_size"] == [2400, 1800]

    with app.app_context():
        row = db.session.get(Examination, exam["id"])
        path = storage.absolute(row.image_path)
        assert row.image_path.endswith(".jpg")
        assert os.path.getsize(path) <= LIMIT
        # The hash is of the upload itself, so the same file is still
        # recognised as a duplicate, and dataset copies as dataset copies.
        assert row.image_sha256 == storage.sha256(raw)
        for derived in (row.model_view_path, row.overlay_path):
            assert os.path.getsize(storage.absolute(derived)) <= LIMIT

    again = examination(client, clinician, record_no="NHR-SIZE-2", data=raw, filename="big.png")
    assert again.get_json()["duplicate_of"]["id"] == exam["id"]

    served = client.get("/api/v1/examinations/%d/image/original" % exam["id"], headers=clinician)
    assert served.status_code == 200 and served.mimetype == "image/jpeg"


def test_shrink_images_brings_existing_records_within_the_limit(app, client, clinician):
    from ear_detection import storage
    from ear_detection.db import Examination, db

    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import manage

    exam = examination(client, clinician, record_no="NHR-SIZE-OLD").get_json()
    with app.app_context():
        row = db.session.get(Examination, exam["id"])
        old_relative, sha = row.image_path, row.image_sha256
        old_path = storage.absolute(old_relative)
    # Simulate an image stored, before the limit existed, at its full size.
    with open(old_path, "wb") as fh:
        fh.write(png(large_photo((2000, 1500))))
    assert os.path.getsize(old_path) > LIMIT

    manage.cmd_shrink_images(argparse.Namespace(dry_run=True, yes=False))
    assert os.path.getsize(old_path) > LIMIT  # a dry run changes nothing

    manage.cmd_shrink_images(argparse.Namespace(dry_run=False, yes=True))
    with app.app_context():
        row = db.session.get(Examination, exam["id"])
        new_path = storage.absolute(row.image_path)
        assert row.image_path != old_relative and row.image_path.endswith(".jpg")
        assert os.path.getsize(new_path) <= LIMIT
        assert (row.image_width, row.image_height) == (2000, 1500)
        assert row.image_sha256 == sha
    assert not os.path.exists(old_path)
