"""Properties of the classifier itself."""

import io

import numpy as np
import pytest
from PIL import Image

from conftest import examination, image_bytes


@pytest.fixture(scope="module")
def active(app):
    return app.extensions["registry"].current()


def test_model_is_exactly_invariant_to_orientation(active):
    # Averaging over all eight rotations and reflections means a rotated input
    # yields the same set of views, so the output is identical up to floating
    # point. Tested on the normalised array, where rotation is exact.
    if not active.meta["use_tta"]:
        pytest.skip("calibration chose single view inference")
    from ear_detection import imaging

    array = imaging.normalize(Image.open(io.BytesIO(image_bytes())).convert("RGB"))
    base, _ = active.classifier.predict(array)
    for turned in (np.rot90(array), np.rot90(array, 2), array[:, ::-1]):
        other, _ = active.classifier.predict(np.ascontiguousarray(turned))
        assert np.abs(base - other).max() < 1e-5


def test_rotated_upload_gives_the_same_answer(client, clinician, active):
    # Rotating the original file shifts centre cropping and resampling by a
    # pixel, so probabilities move slightly; the clinical answer must not.
    if not active.meta["use_tta"]:
        pytest.skip("calibration chose single view inference")
    upright = examination(client, clinician, record_no="NHR-ROT", data=image_bytes()).get_json()
    turned = examination(client, clinician, record_no="NHR-ROT", data=image_bytes(rotate=90)).get_json()
    assert upright["result"]["finding"]["class"] == turned["result"]["finding"]["class"]
    assert upright["result"]["referral"]["recommendation"] == turned["result"]["referral"]["recommendation"]
    a = {r["class"]: r["probability"] for r in upright["result"]["differential"]}
    b = {r["class"]: r["probability"] for r in turned["result"]["differential"]}
    assert max(abs(a[n] - b[n]) for n in a) < 0.01


def test_result_is_deterministic(client, clinician):
    first = examination(client, clinician, record_no="NHR-DET").get_json()["result"]
    second = examination(client, clinician, record_no="NHR-DET").get_json()["result"]
    assert first["differential"] == second["differential"]
