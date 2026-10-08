"""The HTTP API, end to end with the trained model loaded."""

import io
import os
import sys

import numpy as np
import pytest
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

from ear_detection import config, create_app  # noqa: E402

SAMPLE = os.path.join(ROOT, "docs", "images", "normalear.JPG")


@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def sample_bytes(rotate=0):
    if os.path.isfile(SAMPLE):
        image = Image.open(SAMPLE).convert("RGB")
    else:
        yy, xx = np.mgrid[:400, :400]
        disk = np.hypot(xx - 200, yy - 200) < 180
        array = np.zeros((400, 400, 3), np.uint8)
        array[disk] = (200, 140, 130)
        image = Image.fromarray(array)
    if rotate:
        image = image.rotate(rotate, expand=True)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def post(client, data, name="ear.png"):
    return client.post(
        "/api/v1/analyse",
        data={"file": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
    )


def file_snapshot():
    skip = {".venv", ".git", "__pycache__", ".pytest_cache", "Dataset", "normalized"}
    found = set()
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            found.add(os.path.join(base, f))
    return found


def test_health(client):
    body = client.get("/api/v1/health").get_json()
    assert body["status"] == "ok"
    assert body["classes"] == 12


def test_model_card_lists_every_class_with_a_name(client):
    body = client.get("/api/v1/model").get_json()
    assert len(body["classes"]) == 12
    assert all(c["name"] and c["description"] for c in body["classes"])


def test_interface_is_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/js/app.js").status_code == 200


def test_rejects_missing_file(client):
    assert client.post("/api/v1/analyse").status_code == 400


def test_rejects_wrong_extension(client):
    assert post(client, b"not an image", name="notes.txt").status_code == 400


def test_rejects_unreadable_image(client):
    response = post(client, b"\x89PNG\r\n\x1a\n garbage")
    assert response.status_code == 400
    assert "could not be read" in response.get_json()["error"]


def test_rejects_tiny_image(client):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), (200, 100, 100)).save(buffer, format="PNG")
    assert post(client, buffer.getvalue()).status_code == 400


def test_rejects_oversized_upload(client):
    client.application.config["MAX_CONTENT_LENGTH"] = 1024
    try:
        assert post(client, b"x" * 4096).status_code == 413
    finally:
        client.application.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024


def test_analysis_contract(client):
    response = post(client, sample_bytes())
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()

    differential = body["differential"]
    assert len(differential) == 12
    probs = [row["probability"] for row in differential]
    assert probs == sorted(probs, reverse=True)
    assert abs(sum(probs) - 1.0) < 0.01

    referral = body["referral"]
    assert referral["recommendation"] in ("refer", "routine")
    expected = "refer" if referral["abnormal_probability"] >= referral["threshold"] else "routine"
    assert referral["recommendation"] == expected

    assert body["finding"]["class"] == differential[0]["class"]
    assert body["explanation"]["overlay"].startswith("data:image/png;base64,")
    assert body["explanation"]["model_view"].startswith("data:image/png;base64,")


def test_analysis_writes_nothing_to_disk(client):
    before = file_snapshot()
    assert post(client, sample_bytes()).status_code == 200
    created = file_snapshot() - before
    assert not created, "analysis wrote files: %s" % sorted(created)


def test_result_is_deterministic(client):
    a = post(client, sample_bytes()).get_json()["differential"]
    b = post(client, sample_bytes()).get_json()["differential"]
    assert a == b


TTA_ON = config.load_runtime_metadata()["use_tta"]
TTA_REASON = "calibration chose single view inference, which is not rotation invariant"


@pytest.mark.skipif(not TTA_ON, reason=TTA_REASON)
def test_model_is_exactly_invariant_to_orientation():
    # Averaging over all eight rotations and reflections means a rotated
    # input yields the same set of views, so the output is identical up to
    # floating point. Tested on the normalised array, where rotation is exact.
    from ear_detection import imaging
    from ear_detection.model import Classifier

    meta = config.load_runtime_metadata()
    classifier = Classifier(config.MODEL_PATH, meta["classes"],
                            meta["temperature"], meta["use_tta"])
    array = imaging.normalize(Image.open(io.BytesIO(sample_bytes())).convert("RGB"))
    base, _ = classifier.predict(array)
    for turned in (np.rot90(array), np.rot90(array, 2), array[:, ::-1]):
        other, _ = classifier.predict(np.ascontiguousarray(turned))
        assert np.abs(base - other).max() < 1e-5


@pytest.mark.skipif(not TTA_ON, reason=TTA_REASON)
def test_rotated_upload_gives_the_same_answer(client):
    # Rotating the original file changes centre cropping and resampling by a
    # pixel, so probabilities move slightly; the clinical answer must not.
    upright = post(client, sample_bytes()).get_json()
    turned = post(client, sample_bytes(rotate=90)).get_json()
    assert upright["finding"]["class"] == turned["finding"]["class"]
    assert upright["referral"]["recommendation"] == turned["referral"]["recommendation"]
    a = {row["class"]: row["probability"] for row in upright["differential"]}
    b = {row["class"]: row["probability"] for row in turned["differential"]}
    assert max(abs(a[n] - b[n]) for n in a) < 0.01
