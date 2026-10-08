"""Deleting examinations and clearing all records."""

import os

from conftest import ADMIN, examination


def stored(app, exam_id):
    from ear_detection import storage
    from ear_detection.db import Examination, db

    with app.app_context():
        exam = db.session.get(Examination, exam_id)
        return [storage.absolute(p) for p in (exam.image_path, exam.model_view_path, exam.overlay_path)]


def test_clinicians_cannot_delete(client, clinician):
    exam = examination(client, clinician, record_no="NHR-NODEL").get_json()
    assert client.delete("/api/v1/examinations/%d" % exam["id"], headers=clinician,
                         json={"reason": "test"}).status_code == 403
    assert client.post("/api/v1/admin/records/clear", headers=clinician,
                       json={"confirm": "DELETE ALL RECORDS", "password": "x"}).status_code == 403


def test_deleting_an_examination_removes_it_and_its_images(app, client, admin, clinician):
    exam = examination(client, clinician, record_no="NHR-DEL-ONE").get_json()
    client.post("/api/v1/examinations/%d/review" % exam["id"], json={"verdict": "correct"},
                headers=clinician)
    files = stored(app, exam["id"])
    assert all(os.path.isfile(f) for f in files)

    url = "/api/v1/examinations/%d" % exam["id"]
    assert client.delete(url, headers=admin, json={"reason": ""}).status_code == 400

    response = client.delete(url, headers=admin, json={"reason": "Wrong record number"})
    assert response.status_code == 200
    assert response.get_json()["patient_removed"] is True
    assert client.get(url, headers=admin).status_code == 404
    assert not any(os.path.exists(f) for f in files)
    lookup = client.get("/api/v1/patients/NHR-DEL-ONE", headers=admin).get_json()
    assert lookup["exists"] is False

    events = client.get("/api/v1/admin/audit", headers=admin).get_json()["items"]
    deleted = [e for e in events if e["action"] == "examination_deleted" and e["entity_id"] == str(exam["id"])]
    assert deleted and deleted[0]["detail"]["reason"] == "Wrong record number"
    assert deleted[0]["detail"]["reviews_deleted"] == 1


def test_patient_is_kept_while_other_examinations_remain(client, admin, clinician):
    first = examination(client, clinician, record_no="NHR-DEL-TWO", ear="left").get_json()
    examination(client, clinician, record_no="NHR-DEL-TWO", ear="right")
    response = client.delete("/api/v1/examinations/%d" % first["id"], headers=admin,
                             json={"reason": "Duplicate capture"})
    assert response.get_json()["patient_removed"] is False
    assert client.get("/api/v1/patients/NHR-DEL-TWO", headers=admin).get_json()["exists"] is True


def test_clear_all_needs_the_phrase_and_the_password(client, admin, clinician):
    examination(client, clinician, record_no="NHR-CLEAR")
    wrong_phrase = client.post("/api/v1/admin/records/clear", headers=admin,
                               json={"confirm": "delete all", "password": ADMIN[2]})
    assert wrong_phrase.status_code == 400
    wrong_password = client.post("/api/v1/admin/records/clear", headers=admin,
                                 json={"confirm": "DELETE ALL RECORDS", "password": "not-my-password"})
    assert wrong_password.status_code == 403
    assert client.get("/api/v1/admin/records", headers=admin).get_json()["examinations"] > 0


def test_clear_all_removes_records_but_keeps_accounts_models_and_audit(app, client, admin, clinician):
    from ear_detection import storage

    examination(client, clinician, record_no="NHR-CLEAR-2")
    before = client.get("/api/v1/admin/records", headers=admin).get_json()
    assert before["examinations"] > 0 and before["image_files"] > 0
    users_before = len(client.get("/api/v1/admin/users", headers=admin).get_json()["items"])

    response = client.post("/api/v1/admin/records/clear", headers=admin,
                           json={"confirm": "DELETE ALL RECORDS", "password": ADMIN[2]})
    assert response.status_code == 200
    assert response.get_json()["cleared"]["examinations"] == before["examinations"]

    after = client.get("/api/v1/admin/records", headers=admin).get_json()
    assert after["patients"] == after["examinations"] == after["reviews"] == after["image_files"] == 0
    assert not os.path.exists(storage.absolute("images"))

    assert len(client.get("/api/v1/admin/users", headers=admin).get_json()["items"]) == users_before
    assert client.get("/api/v1/health").get_json()["model_version"]
    actions = [e["action"] for e in client.get("/api/v1/admin/audit?limit=500", headers=admin).get_json()["items"]]
    assert "records_cleared" in actions and "examination_created" in actions

    # The system keeps working on an empty record.
    assert examination(client, clinician, record_no="NHR-AFTER").status_code == 201
