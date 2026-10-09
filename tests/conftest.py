"""Shared test setup.

Tests never touch the real database or patient storage. Environment variables
are set here, before any project module is imported, so the settings in .env
are ignored (python-dotenv never overrides variables that are already set).
"""

import io
import os
import shutil
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, "src"))

_TMP = tempfile.mkdtemp(prefix="ear_tests_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "test.sqlite").replace("\\", "/")
os.environ["JWT_SECRET_KEY"] = "test-secret-key-that-is-long-enough-0123456789"
os.environ["STORAGE_DIR"] = os.path.join(_TMP, "storage")
os.environ["MIN_REVIEWS_FOR_RETRAIN"] = "3"
# The tests drive the backtest directly; no background scheduler.
os.environ["BACKTEST_ENABLED"] = "0"
# Password sign in by default, whatever the real .env says. The Google tests
# switch it on for themselves.
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

SAMPLE = os.path.join(ROOT, "docs", "images", "normalear.JPG")

ADMIN = ("admin_user", "Admin Person", "Admin-Pass-2026")
CLINICIAN = ("clinician_user", "Clinician Person", "Clinic-Pass-2026")


def pytest_unconfigure(config):
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def app():
    from ear_detection import create_app
    from ear_detection.auth import hash_password
    from ear_detection.db import User, db

    application = create_app()
    application.config["TESTING"] = True
    with application.app_context():
        for username, name, password in (ADMIN, CLINICIAN):
            db.session.add(User(username=username, full_name=name,
                                role="admin" if username == ADMIN[0] else "clinician",
                                password_hash=hash_password(password),
                                must_change_password=False))
        db.session.commit()
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, username, password):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.get_json()
    return {"Authorization": "Bearer " + response.get_json()["access_token"]}


@pytest.fixture
def clinician(client):
    return login(client, CLINICIAN[0], CLINICIAN[2])


@pytest.fixture
def admin(client):
    return login(client, ADMIN[0], ADMIN[2])


def image_bytes(rotate=0, size=None, fmt="PNG"):
    from PIL import Image

    image = Image.open(SAMPLE).convert("RGB")
    if rotate:
        image = image.rotate(rotate, expand=True)
    if size:
        image = image.resize(size)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def examination(client, headers, record_no="NHR-0001", sex="female", ear="left", age="34",
                data=None, filename="scan.png"):
    form = {"record_no": record_no, "age": age, "sex": sex, "ear": ear, "notes": "",
            "file": (io.BytesIO(data if data is not None else image_bytes()), filename)}
    return client.post("/api/v1/examinations", data=form, headers=headers,
                       content_type="multipart/form-data")
