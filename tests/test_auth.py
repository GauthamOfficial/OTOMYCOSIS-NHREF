"""Sign in, sessions, password rules and lockout."""

from conftest import ADMIN, CLINICIAN, login


def make_user(app, username, password, role="clinician", must_change=False):
    from ear_detection.auth import hash_password
    from ear_detection.db import User, db

    with app.app_context():
        user = User(username=username, full_name=username.title(), role=role,
                    password_hash=hash_password(password), must_change_password=must_change)
        db.session.add(user)
        db.session.commit()
        return user.id


def test_wrong_password_and_unknown_user_get_the_same_answer(client):
    wrong = client.post("/api/v1/auth/login", json={"username": CLINICIAN[0], "password": "nope-12345"})
    unknown = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "nope-12345"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.get_json()["error"] == unknown.get_json()["error"]


def test_api_requires_a_token(client):
    assert client.get("/api/v1/examinations").status_code == 401
    assert client.get("/api/v1/examinations",
                      headers={"Authorization": "Bearer not.a.token"}).status_code == 401


def test_health_is_public_and_model_card_is_not(client):
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/model").status_code == 401


def test_refresh_cookie_is_httponly_and_needs_the_csrf_header(app, client):
    response = client.post("/api/v1/auth/login",
                           json={"username": CLINICIAN[0], "password": CLINICIAN[2]})
    cookies = response.headers.getlist("Set-Cookie")
    refresh_cookie = [c for c in cookies if c.startswith("refresh_token_cookie=")][0]
    assert "HttpOnly" in refresh_cookie and "SameSite=Strict" in refresh_cookie
    assert "Path=/api/v1/auth" in refresh_cookie

    # Without the double submit header the refresh is refused.
    assert client.post("/api/v1/auth/refresh").status_code == 401
    csrf = client.get_cookie("csrf_refresh_token").value
    refreshed = client.post("/api/v1/auth/refresh", headers={"X-CSRF-TOKEN": csrf})
    assert refreshed.status_code == 200
    assert refreshed.get_json()["access_token"]


def test_lockout_after_repeated_failures(app, client):
    make_user(app, "locky", "Locky-Pass-2026")
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"username": "locky", "password": "wrong-pass-1"})
    response = client.post("/api/v1/auth/login", json={"username": "locky", "password": "Locky-Pass-2026"})
    assert response.status_code == 403
    assert response.get_json()["code"] == "locked"


def test_temporary_password_blocks_the_api_until_changed(app, client):
    make_user(app, "newbie", "Temp-Pass-2026", must_change=True)
    headers = login(client, "newbie", "Temp-Pass-2026")
    blocked = client.get("/api/v1/examinations", headers=headers)
    assert blocked.status_code == 403
    assert blocked.get_json()["code"] == "password_change_required"

    changed = client.post("/api/v1/auth/password", headers=headers,
                          json={"current_password": "Temp-Pass-2026", "new_password": "Own-Pass-2026x"})
    assert changed.status_code == 200
    fresh = {"Authorization": "Bearer " + changed.get_json()["access_token"]}
    assert client.get("/api/v1/examinations", headers=fresh).status_code == 200
    # The token issued before the change no longer works.
    assert client.get("/api/v1/examinations", headers=headers).status_code == 401


def test_weak_passwords_are_refused(app, client):
    make_user(app, "weakling", "Good-Pass-2026")
    headers = login(client, "weakling", "Good-Pass-2026")
    for bad in ("short1", "alllettersonly", "1234567890123", "weakling"):
        response = client.post("/api/v1/auth/password", headers=headers,
                               json={"current_password": "Good-Pass-2026", "new_password": bad})
        assert response.status_code == 400, bad


def test_deactivation_ends_the_session_immediately(app, client):
    user_id = make_user(app, "leaver", "Leaver-Pass-2026")
    headers = login(client, "leaver", "Leaver-Pass-2026")
    assert client.get("/api/v1/examinations", headers=headers).status_code == 200

    admin = login(client, ADMIN[0], ADMIN[2])
    assert client.patch("/api/v1/admin/users/%d" % user_id, headers=admin,
                        json={"is_active": False}).status_code == 200
    assert client.get("/api/v1/examinations", headers=headers).status_code == 401


def test_clinicians_cannot_reach_admin_routes(client, clinician):
    assert client.get("/api/v1/admin/users", headers=clinician).status_code == 403
    assert client.post("/api/v1/admin/retrain", headers=clinician).status_code == 403


def test_forged_client_address_is_not_written_to_the_audit_log(app, client):
    from ear_detection.db import AuditEvent

    client.post("/api/v1/auth/login", json={"username": CLINICIAN[0], "password": CLINICIAN[2]},
                headers={"X-Real-IP": "203.0.113.66"})
    with app.app_context():
        event = AuditEvent.query.filter_by(action="login").order_by(AuditEvent.id.desc()).first()
        assert event.ip != "203.0.113.66"
