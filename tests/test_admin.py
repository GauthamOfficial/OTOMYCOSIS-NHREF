"""User administration, model versions, the approval gate and the audit log."""

from conftest import ADMIN, examination


def test_admin_creates_a_clinician_with_a_temporary_password(client, admin):
    response = client.post("/api/v1/admin/users", headers=admin, json={
        "username": "dr.silva", "full_name": "Dr Silva", "role": "clinician",
        "password": "Temp-Pass-2026"})
    assert response.status_code == 201
    assert response.get_json()["user"]["must_change_password"] is True

    taken = client.post("/api/v1/admin/users", headers=admin, json={
        "username": "dr.silva", "full_name": "Other", "role": "clinician", "password": "Temp-Pass-2026"})
    assert taken.status_code == 409


def test_user_input_is_validated(client, admin):
    def attempt(**fields):
        body = {"username": "valid.name", "full_name": "Valid Name", "role": "clinician",
                "password": "Valid-Pass-2026"}
        body.update(fields)
        return client.post("/api/v1/admin/users", headers=admin, json=body)

    assert attempt(username="No Spaces").status_code == 400
    assert attempt(role="superuser").status_code == 400
    assert attempt(password="short").status_code == 400
    assert attempt(full_name="").status_code == 400


def test_an_admin_cannot_lock_themselves_out(app, client, admin):
    from ear_detection.db import User

    with app.app_context():
        me = User.query.filter_by(username=ADMIN[0]).first().id
    assert client.patch("/api/v1/admin/users/%d" % me, headers=admin,
                        json={"is_active": False}).status_code == 400
    assert client.patch("/api/v1/admin/users/%d" % me, headers=admin,
                        json={"role": "clinician"}).status_code == 400


def test_password_reset_forces_a_change(app, client, admin):
    created = client.post("/api/v1/admin/users", headers=admin, json={
        "username": "forgetful", "full_name": "Forgetful", "role": "clinician",
        "password": "First-Pass-2026"}).get_json()["user"]
    reset = client.post("/api/v1/admin/users/%d/reset-password" % created["id"], headers=admin,
                        json={"password": "Second-Pass-2026"})
    assert reset.status_code == 200
    assert reset.get_json()["user"]["must_change_password"] is True
    login = client.post("/api/v1/auth/login", json={"username": "forgetful", "password": "Second-Pass-2026"})
    assert login.status_code == 200


def test_models_and_retraining_readiness(client, admin):
    data = client.get("/api/v1/admin/models", headers=admin).get_json()
    assert data["active"]
    assert any(v["status"] == "active" for v in data["versions"])
    readiness = data["readiness"]
    assert set(readiness) >= {"usable_reviews", "minimum_reviews", "dataset_available", "ready", "problems"}


def test_a_candidate_that_fails_the_gate_cannot_go_live(app, client, admin):
    from ear_detection.db import ModelVersion, db

    with app.app_context():
        active = ModelVersion.query.filter_by(status="active").first()
        failing = ModelVersion(version="9.1.0", status="candidate", model_path=active.model_path,
                               calibration_path=active.calibration_path,
                               evaluation_path=active.evaluation_path,
                               summary={"gate": {"passed": False, "reasons": ["worse"]}})
        db.session.add(failing)
        db.session.commit()
        failing_id = failing.id

    assert client.post("/api/v1/admin/models/%d/approve" % failing_id, headers=admin).status_code == 409
    assert client.post("/api/v1/admin/models/%d/reject" % failing_id, headers=admin).status_code == 200


def test_approving_a_passing_candidate_switches_the_model(app, client, admin, clinician):
    from ear_detection.db import ModelVersion, db

    with app.app_context():
        active = ModelVersion.query.filter_by(status="active").first()
        original_id = active.id
        passing = ModelVersion(version="9.2.0", status="candidate", model_path=active.model_path,
                               calibration_path=active.calibration_path,
                               evaluation_path=active.evaluation_path,
                               summary={"gate": {"passed": True, "reasons": []}})
        db.session.add(passing)
        db.session.commit()
        passing_id = passing.id

    assert client.post("/api/v1/admin/models/%d/approve" % passing_id, headers=admin).status_code == 200
    assert client.get("/api/v1/health").get_json()["model_version"] == "9.2.0"
    exam = examination(client, clinician, record_no="NHR-NEWMODEL").get_json()
    assert exam["model_version"] == "9.2.0"

    # Restore the original version for the remaining tests.
    with app.app_context():
        for version in ModelVersion.query.filter_by(status="active").all():
            version.status = "retired"
        original = db.session.get(ModelVersion, original_id)
        original.status = "active"
        db.session.commit()
        app.extensions["registry"].activate(original)


def test_actions_are_audited(client, admin, clinician):
    examination(client, clinician, record_no="NHR-AUDIT")
    actions = [e["action"] for e in client.get("/api/v1/admin/audit", headers=admin).get_json()["items"]]
    assert "login" in actions
    assert "examination_created" in actions


def test_copies_of_frozen_test_images_are_never_trainable(app, client, clinician):
    import csv
    import os

    import pytest

    from ear_detection import retraining

    if not os.path.isfile(retraining.MANIFEST):
        pytest.skip("dataset manifest not present")
    with open(retraining.MANIFEST, newline="", encoding="utf-8") as fh:
        test_row = next(r for r in csv.DictReader(fh) if r["split"] == "test")
    path = os.path.join(retraining.config.BASE_DIR, test_row["path"])
    if not os.path.isfile(path):
        pytest.skip("dataset images not present")

    with open(path, "rb") as fh:
        data = fh.read()
    with app.app_context():
        before = retraining.usable_reviews()
    exam = examination(client, clinician, record_no="NHR-FROZEN", data=data,
                       filename="frozen" + os.path.splitext(path)[1]).get_json()
    client.post("/api/v1/examinations/%d/review" % exam["id"], json={"verdict": "correct"},
                headers=clinician)
    with app.app_context():
        assert retraining.usable_reviews() == before
