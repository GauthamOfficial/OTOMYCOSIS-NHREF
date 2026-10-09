"""Images from cameras and endoscopes, and the schema upgrade they needed."""

import io
import os

from conftest import _TMP, image_bytes


def submit(client, headers, **fields):
    form = {"record_no": "NHR-CAM", "age": "50", "sex": "female", "ear": "left",
            "file": (io.BytesIO(image_bytes()), "capture-20261009-101500.jpg")}
    form.update(fields)
    return client.post("/api/v1/examinations", data=form, headers=headers,
                       content_type="multipart/form-data")


def test_camera_capture_records_its_source_and_device(client, clinician):
    response = submit(client, clinician, image_source="camera",
                      capture_device="  USB   Video Otoscope (0c45:6366) ")
    assert response.status_code == 201
    exam = response.get_json()
    assert exam["image_source"] == "camera"
    assert exam["capture_device"] == "USB Video Otoscope (0c45:6366)"

    stored = client.get("/api/v1/examinations/%d" % exam["id"], headers=clinician).get_json()
    assert stored["image_source"] == "camera"


def test_uploads_are_the_default_and_never_keep_a_device(client, clinician):
    exam = submit(client, clinician, record_no="NHR-UP", capture_device="Sneaky label").get_json()
    assert exam["image_source"] == "upload"
    assert exam["capture_device"] is None


def test_unknown_sources_are_refused(client, clinician):
    response = submit(client, clinician, record_no="NHR-BADSRC", image_source="scanner")
    assert response.status_code == 400
    assert response.get_json()["field"] == "image_source"


def test_statistics_report_agreement_per_source(client, clinician):
    exam = submit(client, clinician, record_no="NHR-SRC-STAT", image_source="camera",
                  capture_device="Test Endoscope").get_json()
    client.post("/api/v1/examinations/%d/review" % exam["id"], json={"verdict": "correct"},
                headers=clinician)
    sources = client.get("/api/v1/stats", headers=clinician).get_json()["by_source"]
    row = next(s for s in sources if s["source"] == "Camera: Test Endoscope")
    assert row["cases"] >= 1
    assert row["agreement"]["value"] == 1.0


def test_schema_upgrade_adds_missing_columns_and_is_safe_to_repeat():
    from sqlalchemy import inspect, text

    from ear_detection.db import db, make_db_app, upgrade_schema

    path = os.path.join(_TMP, "old_schema.sqlite").replace("\\", "/")
    app = make_db_app("sqlite:///" + path)
    with app.app_context():
        db.create_all()
        # Recreate a database from before the camera feature.
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE examinations DROP COLUMN capture_device"))
            conn.execute(text("ALTER TABLE examinations DROP COLUMN image_source"))
        columns = {c["name"] for c in inspect(db.engine).get_columns("examinations")}
        assert "image_source" not in columns

        added = upgrade_schema()
        assert set(added) == {"examinations.image_source", "examinations.capture_device"}
        assert upgrade_schema() == []
        columns = {c["name"] for c in inspect(db.engine).get_columns("examinations")}
        assert {"image_source", "capture_device"} <= columns
        db.engine.dispose()
