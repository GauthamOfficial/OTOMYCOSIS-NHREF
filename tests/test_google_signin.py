"""Signing in with a Google account.

Google's own verification is replaced here, so the tests never reach the
network. What is tested is everything this system decides once a token has
been verified: who is let in, who is not, and what is recorded.
"""

import pytest

from conftest import ADMIN, CLINICIAN

CLIENT_ID = "912345678901-testclient.apps.googleusercontent.com"


@pytest.fixture
def google(app, monkeypatch):
    """Turn Google sign in on, with a stand in for Google's verification."""
    from ear_detection import admin, auth, config

    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", CLIENT_ID)
    issued = {}

    def fake_verify(credential):
        if credential not in issued:
            raise ValueError("Token has wrong audience.")
        return issued[credential]

    monkeypatch.setattr(auth, "verify_google_credential", fake_verify)
    monkeypatch.setattr(admin.config, "GOOGLE_CLIENT_ID", CLIENT_ID)

    def token(email, sub=None, verified=True):
        # Google gives each account its own permanent subject id.
        sub = sub or "google-sub-" + email
        key = "token-for-%s-%s" % (email, sub)
        issued[key] = {"email": email, "email_verified": verified, "sub": sub,
                       "aud": CLIENT_ID, "name": "Test Person"}
        return key

    return token


def sign_in(client, credential):
    return client.post("/api/v1/auth/google", json={"credential": credential})


def add_user(client, admin_headers, email, role="clinician", password=None):
    body = {"email": email, "full_name": "Dr Google", "role": role}
    if password:
        body["password"] = password
    return client.post("/api/v1/admin/users", json=body, headers=admin_headers)


# ---------------- what the sign in page is told ----------------

def test_the_page_is_told_whether_google_is_set_up(client, google):
    conf = client.get("/api/v1/auth/config").get_json()
    assert conf["google_enabled"] is True
    assert conf["google_client_id"] == CLIENT_ID
    assert conf["password_roles"] == ["admin"]


def test_without_a_client_id_nothing_changes(client):
    assert client.get("/api/v1/auth/config").get_json()["google_enabled"] is False
    assert sign_in(client, "anything").status_code == 400


# ---------------- signing in ----------------

def test_a_registered_google_account_signs_in(client, admin, google):
    add_user(client, admin, "dr.google@gmail.com")
    response = sign_in(client, google("dr.google@gmail.com"))
    assert response.status_code == 200
    body = response.get_json()
    assert body["user"]["email"] == "dr.google@gmail.com"
    # No password was ever set, so nothing is demanded at first sign in.
    assert body["user"]["must_change_password"] is False
    assert body["access_token"]

    headers = {"Authorization": "Bearer " + body["access_token"]}
    assert client.get("/api/v1/examinations", headers=headers).status_code == 200


def test_an_account_nobody_added_is_refused(client, google):
    response = sign_in(client, google("stranger@gmail.com"))
    assert response.status_code == 403
    assert "no account for stranger@gmail.com" in response.get_json()["error"]


def test_an_unverified_or_forged_token_is_refused(client, admin, google):
    add_user(client, admin, "unverified@gmail.com")
    assert sign_in(client, "made-up-token").status_code == 401

    from ear_detection import auth

    def unverified(credential):
        raise ValueError("The email address on that Google account is not verified.")

    original = auth.verify_google_credential
    auth.verify_google_credential = unverified
    try:
        refused = sign_in(client, "whatever")
        assert refused.status_code == 401
        assert "not verified" in refused.get_json()["error"]
    finally:
        auth.verify_google_credential = original


def test_a_deactivated_account_cannot_come_back_through_google(app, client, admin, google):
    from ear_detection.db import User, db

    created = add_user(client, admin, "leaver@gmail.com").get_json()["user"]
    assert sign_in(client, google("leaver@gmail.com")).status_code == 200
    client.patch("/api/v1/admin/users/%d" % created["id"], json={"is_active": False},
                 headers=admin)
    refused = sign_in(client, google("leaver@gmail.com"))
    assert refused.status_code == 403
    assert "deactivated" in refused.get_json()["error"]
    with app.app_context():
        db.session.delete(db.session.get(User, created["id"]))
        db.session.commit()


def test_the_address_is_tied_to_the_google_account_that_first_used_it(client, admin, google):
    add_user(client, admin, "linked@gmail.com")
    assert sign_in(client, google("linked@gmail.com", sub="first-account")).status_code == 200
    # The same address presented by a different Google account is refused.
    second = sign_in(client, google("linked@gmail.com", sub="second-account"))
    assert second.status_code == 403
    assert "does not match" in second.get_json()["error"]
    # The original still works.
    assert sign_in(client, google("linked@gmail.com", sub="first-account")).status_code == 200


def test_one_google_account_cannot_hold_two_records(client, admin, google):
    add_user(client, admin, "one@gmail.com")
    add_user(client, admin, "two@gmail.com")
    assert sign_in(client, google("one@gmail.com", sub="shared")).status_code == 200
    assert sign_in(client, google("two@gmail.com", sub="shared")).status_code == 403


def test_signing_in_with_google_clears_a_password_lockout(app, client, admin, google):
    from ear_detection.db import User, db, utcnow
    from datetime import timedelta

    add_user(client, admin, "locked.out@gmail.com")
    with app.app_context():
        user = User.query.filter_by(username="locked.out@gmail.com").one()
        user.failed_logins = 5
        user.locked_until = utcnow() + timedelta(minutes=15)
        db.session.commit()

    assert sign_in(client, google("locked.out@gmail.com")).status_code == 200
    with app.app_context():
        user = User.query.filter_by(username="locked.out@gmail.com").one()
        assert user.locked_until is None and user.failed_logins == 0
        assert user.google_sub and user.google_linked_at


def test_sign_ins_and_refusals_are_recorded(client, admin, google):
    add_user(client, admin, "audited@gmail.com")
    sign_in(client, google("audited@gmail.com"))
    sign_in(client, google("not.invited@gmail.com"))

    events = client.get("/api/v1/admin/audit?limit=50", headers=admin).get_json()["items"]
    refused = next(e for e in events if e["action"] == "login_failed"
                   and (e["detail"] or {}).get("reason") == "unknown address")
    assert refused["detail"]["email"] == "not.invited@gmail.com"
    assert any(e["action"] == "login" and (e["detail"] or {}).get("via") == "google"
               for e in events)
    assert any(e["action"] == "google_linked" for e in events)


# ---------------- the password fallback ----------------

def test_clinicians_cannot_use_a_password_once_google_is_on(client, google):
    response = client.post("/api/v1/auth/login",
                           json={"email": CLINICIAN[0], "password": CLINICIAN[2]})
    assert response.status_code == 403
    assert "Sign in with Google" in response.get_json()["error"]


def test_administrators_keep_their_password_for_emergencies(client, google):
    response = client.post("/api/v1/auth/login",
                           json={"email": ADMIN[0], "password": ADMIN[2]})
    assert response.status_code == 200
    assert response.get_json()["access_token"]


def test_a_wrong_password_still_says_only_that(client, google):
    response = client.post("/api/v1/auth/login",
                           json={"email": ADMIN[0], "password": "not-the-password"})
    assert response.status_code == 401
    assert response.get_json()["error"] == "Incorrect email address or password."


# ---------------- adding people ----------------

def test_a_clinician_can_be_added_without_any_password(app, client, admin, google):
    from ear_detection.db import User

    created = add_user(client, admin, "nopassword@gmail.com")
    assert created.status_code == 201
    assert created.get_json()["user"]["must_change_password"] is False
    with app.app_context():
        user = User.query.filter_by(username="nopassword@gmail.com").one()
        # A hash exists so the column stays valid, but nobody knows it.
        assert user.password_hash and not user.google_linked_at
    assert sign_in(client, google("nopassword@gmail.com")).status_code == 200


def test_an_administrator_can_still_be_given_an_emergency_password(client, admin, google):
    add_user(client, admin, "emergency.admin@gmail.com", role="admin",
             password="Emergency-Pass-2026")
    response = client.post("/api/v1/auth/login", json={"email": "emergency.admin@gmail.com",
                                                       "password": "Emergency-Pass-2026"})
    assert response.status_code == 200
    assert response.get_json()["user"]["must_change_password"] is True


def test_a_password_is_still_required_when_google_is_off(client, admin):
    assert add_user(client, admin, "nogoogle@gmail.com").status_code == 400
