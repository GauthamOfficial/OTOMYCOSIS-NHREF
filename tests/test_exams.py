"""Examinations, reviews, history, statistics and privacy."""

import io
import logging
import os

import pytest
from PIL import Image

from conftest import examination, image_bytes


def all_files(root):
    found = set()
    for base, _dirs, files in os.walk(root):
        for f in files:
            found.add(os.path.join(base, f))
    return found


def test_examination_is_analysed_and_recorded(client, clinician):
    response = examination(client, clinician, record_no="nhr 1001")
    assert response.status_code == 201
    exam = response.get_json()
    assert exam["record_no"] == "NHR1001"
    assert exam["status"] == "unreviewed"
    assert exam["model_version"]

    result = exam["result"]
    probs = [row["probability"] for row in result["differential"]]
    # The prediction always heads the differential.
    assert result["differential"][0]["class"] == result["finding"]["class"]
    assert probs == sorted(probs, reverse=True)
    assert abs(sum(probs) - 1.0) < 0.01
    expected = "refer" if result["referral"]["abnormal_probability"] >= result["referral"]["threshold"] \
        else "routine"
    assert result["referral"]["recommendation"] == expected


def test_images_are_stored_privately_under_random_names(app, client, clinician, caplog):
    from ear_detection import config

    secret_name = "Kamala Wijesinghe ear photo.png"
    before = all_files(config.STORAGE_DIR)
    with caplog.at_level(logging.DEBUG):
        response = examination(client, clinician, record_no="NHR-PRIV", filename=secret_name)
    assert response.status_code == 201
    created = all_files(config.STORAGE_DIR) - before

    assert len(created) == 3, "original, model view and overlay"
    assert all(os.path.realpath(p).startswith(os.path.realpath(config.STORAGE_DIR)) for p in created)
    assert not any("Kamala" in p for p in created)
    assert "Kamala" not in caplog.text, "an uploaded filename reached the logs"

    from ear_detection.db import AuditEvent, Examination
    with app.app_context():
        exam = Examination.query.order_by(Examination.id.desc()).first()
        assert "Kamala" not in exam.image_path
        for event in AuditEvent.query.all():
            assert "Kamala" not in str(event.detail)


def test_images_need_a_session(client, clinician):
    exam = examination(client, clinician, record_no="NHR-IMG").get_json()
    url = exam["images"]["original"]
    assert client.get(url).status_code == 401
    response = client.get(url, headers=clinician)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get(url.replace("original", "../../etc"), headers=clinician).status_code == 404


@pytest.mark.parametrize("field,value,expect", [
    ("record_no", "", "record_no"),
    ("record_no", "has spaces;drop", "record_no"),
    ("age", "abc", "age"),
    ("age", "130", "age"),
    ("sex", "unknown", "sex"),
    ("ear", "both", "ear"),
])
def test_patient_details_are_validated(client, clinician, field, value, expect):
    form = {"record_no": "NHR-VAL", "age": "40", "sex": "male", "ear": "right",
            "file": (io.BytesIO(image_bytes()), "scan.png")}
    form[field] = value
    response = client.post("/api/v1/examinations", data=form, headers=clinician,
                           content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.get_json()["field"] == expect


def test_bad_images_are_refused(client, clinician):
    assert examination(client, clinician, data=b"text", filename="notes.txt").status_code == 400
    assert examination(client, clinician, data=b"\x89PNG broken").status_code == 400
    tiny = io.BytesIO()
    Image.new("RGB", (40, 40)).save(tiny, format="PNG")
    assert examination(client, clinician, data=tiny.getvalue()).status_code == 400


def test_a_record_number_cannot_change_sex(client, clinician):
    assert examination(client, clinician, record_no="NHR-SEX", sex="female").status_code == 201
    response = examination(client, clinician, record_no="NHR-SEX", sex="male")
    assert response.status_code == 409


def test_patient_lookup(client, clinician):
    examination(client, clinician, record_no="NHR-LOOK", sex="male")
    found = client.get("/api/v1/patients/nhr-look", headers=clinician).get_json()
    assert found["exists"] and found["sex"] == "male" and found["examinations"] >= 1
    assert client.get("/api/v1/patients/NHR-NOBODY", headers=clinician).get_json()["exists"] is False


def test_repeated_image_is_flagged(client, clinician):
    data = image_bytes(size=(300, 300))
    examination(client, clinician, record_no="NHR-DUP", data=data)
    second = examination(client, clinician, record_no="NHR-DUP", data=data).get_json()
    assert second["duplicate_of"]["id"] < second["id"]


def review(client, headers, exam_id, **payload):
    return client.post("/api/v1/examinations/%d/review" % exam_id, json=payload, headers=headers)


def test_review_confirm_correct_and_other(client, clinician):
    exam = examination(client, clinician, record_no="NHR-REV").get_json()
    predicted = exam["result"]["finding"]["class"]
    other_class = exam["result"]["differential"][1]["class"]

    confirmed = review(client, clinician, exam["id"], verdict="correct").get_json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["review"]["label"] == predicted

    # Picking the predicted class from the list counts as confirming it.
    same = review(client, clinician, exam["id"], verdict="corrected", true_class=predicted).get_json()
    assert same["review"]["verdict"] == "correct"

    corrected = review(client, clinician, exam["id"], verdict="corrected",
                       true_class=other_class, comment="Second opinion").get_json()
    assert corrected["status"] == "corrected"
    assert corrected["review"]["label"] == other_class

    assert review(client, clinician, exam["id"], verdict="other").status_code == 400
    other = review(client, clinician, exam["id"], verdict="other", other_label="  Otomycosis ").get_json()
    assert other["status"] == "other"
    assert other["review"]["other_label"] == "Otomycosis"
    assert other["review"]["label"] is None

    # Reviews are append only: every verdict is kept.
    assert len(other["reviews"]) == 4


def test_review_rejects_unknown_input(client, clinician):
    exam = examination(client, clinician, record_no="NHR-BAD").get_json()
    assert review(client, clinician, exam["id"], verdict="maybe").status_code == 400
    assert review(client, clinician, exam["id"], verdict="corrected", true_class="flu").status_code == 400
    assert review(client, clinician, 999999, verdict="correct").status_code == 404


def test_history_filters(client, clinician):
    a = examination(client, clinician, record_no="NHR-HIST-A").get_json()
    b = examination(client, clinician, record_no="NHR-HIST-B").get_json()
    review(client, clinician, b["id"], verdict="other", other_label="Exostosis")

    def ids(**params):
        query = "&".join("%s=%s" % kv for kv in params.items())
        return {i["id"] for i in client.get("/api/v1/examinations?" + query, headers=clinician)
                .get_json()["items"]}

    assert {a["id"], b["id"]} <= ids(q="HIST", per_page=100)
    assert a["id"] in ids(status="unreviewed", per_page=100)
    assert b["id"] not in ids(status="unreviewed", per_page=100)
    assert b["id"] in ids(status="disagreement", per_page=100)
    assert a["id"] not in ids(status="reviewed", per_page=100)


def test_statistics_follow_the_reviews(client, clinician):
    before = client.get("/api/v1/stats", headers=clinician).get_json()
    exam = examination(client, clinician, record_no="NHR-STAT").get_json()
    review(client, clinician, exam["id"], verdict="correct")
    after = client.get("/api/v1/stats", headers=clinician).get_json()
    assert after["totals"]["examinations"] == before["totals"]["examinations"] + 1
    assert after["totals"]["reviewed"] == before["totals"]["reviewed"] + 1
    assert after["agreement"]["total"] == before["agreement"]["total"] + 1
    assert len(after["per_class"]) == 12


def test_stored_examination_renders_like_the_live_one(client, clinician):
    live = examination(client, clinician, record_no="NHR-SAME").get_json()
    stored = client.get("/api/v1/examinations/%d" % live["id"], headers=clinician).get_json()
    assert stored["result"]["finding"] == live["result"]["finding"]
    assert stored["result"]["referral"] == live["result"]["referral"]
    assert stored["result"]["differential"] == live["result"]["differential"]
