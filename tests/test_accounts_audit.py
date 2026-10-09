"""Accounts identified by email address, and clearing the audit log."""

from datetime import timedelta

from conftest import ADMIN, CLINICIAN, login


def add(client, admin, email, **fields):
    body = {"email": email, "full_name": "Dr Perera", "role": "clinician",
            "password": "Temp-Pass-2026"}
    body.update(fields)
    return client.post("/api/v1/admin/users", json=body, headers=admin)


# ---------------- email as the sign in name ----------------

def test_a_user_is_added_by_email_and_signs_in_with_it(client, admin):
    created = add(client, admin, "  Dr.Perera@Gmail.com ")
    assert created.status_code == 201
    user = created.get_json()["user"]
    assert user["email"] == "dr.perera@gmail.com"

    signed_in = client.post("/api/v1/auth/login",
                            json={"email": "dr.perera@gmail.com", "password": "Temp-Pass-2026"})
    assert signed_in.status_code == 200
    assert signed_in.get_json()["user"]["must_change_password"] is True


def test_addresses_that_are_not_valid_are_refused(client, admin):
    for bad in ("dr.perera", "dr perera@gmail.com", "@gmail.com", "dr@", "", "a@b"):
        response = add(client, admin, bad)
        assert response.status_code == 400, bad
        assert response.get_json()["field"] == "email"
    assert add(client, admin, "x" * 60 + "@gmail.com").status_code == 400


def test_an_address_can_only_belong_to_one_account(client, admin):
    add(client, admin, "shared@nhref.lk")
    again = add(client, admin, "SHARED@nhref.lk", full_name="Someone Else")
    assert again.status_code == 409
    assert again.get_json()["field"] == "email"


def test_sign_in_names_from_before_the_email_rule_still_work(client):
    # The fixture accounts predate it, as a real installation's would.
    assert login(client, CLINICIAN[0], CLINICIAN[2])


def test_an_administrator_changes_the_address_and_the_role(app, client, admin):
    user = add(client, admin, "old.address@gmail.com").get_json()["user"]
    headers = login(client, "old.address@gmail.com", "Temp-Pass-2026")

    changed = client.patch("/api/v1/admin/users/%d" % user["id"],
                           json={"email": "New.Address@nhref.lk", "role": "admin"}, headers=admin)
    assert changed.status_code == 200
    assert changed.get_json()["user"]["email"] == "new.address@nhref.lk"
    assert changed.get_json()["user"]["role"] == "admin"

    # The old address no longer signs in, the new one does, and the session
    # opened under the old address has ended.
    assert client.post("/api/v1/auth/login",
                       json={"email": "old.address@gmail.com",
                             "password": "Temp-Pass-2026"}).status_code == 401
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 401
    assert login(client, "new.address@nhref.lk", "Temp-Pass-2026")


def test_an_address_already_in_use_is_refused_on_change(client, admin):
    first = add(client, admin, "taken.one@gmail.com").get_json()["user"]
    add(client, admin, "taken.two@gmail.com")
    clash = client.patch("/api/v1/admin/users/%d" % first["id"],
                         json={"email": "taken.two@gmail.com"}, headers=admin)
    assert clash.status_code == 409


def test_clinicians_cannot_manage_accounts(client, clinician):
    assert add(client, clinician, "nope@gmail.com").status_code == 403


# ---------------- clearing the audit log ----------------

def events(client, admin):
    return client.get("/api/v1/admin/audit?limit=500", headers=admin).get_json()


def old_event(app, days):
    from ear_detection.db import AuditEvent, db, utcnow

    with app.app_context():
        db.session.add(AuditEvent(action="login", at=utcnow() - timedelta(days=days)))
        db.session.commit()


def test_the_log_reports_its_size(client, admin):
    data = events(client, admin)
    assert data["total"] >= 1 and data["oldest"]
    assert data["confirm_phrase"] == "CLEAR AUDIT LOG"


def test_clearing_needs_the_right_password(client, admin):
    before = events(client, admin)["total"]
    wrong = client.post("/api/v1/admin/audit/clear", headers=admin,
                        json={"older_than_days": 90, "password": "not-my-password"})
    assert wrong.status_code == 403
    assert events(client, admin)["total"] >= before


def test_clearing_everything_needs_the_phrase(client, admin):
    response = client.post("/api/v1/admin/audit/clear", headers=admin,
                           json={"older_than_days": 0, "password": ADMIN[2], "confirm": "yes"})
    assert response.status_code == 400
    assert response.get_json()["field"] == "confirm"


def test_only_entries_past_the_chosen_age_are_removed(app, client, admin):
    old_event(app, days=200)
    old_event(app, days=40)
    before = events(client, admin)["total"]

    response = client.post("/api/v1/admin/audit/clear", headers=admin,
                           json={"older_than_days": 90, "password": ADMIN[2]})
    assert response.status_code == 200
    assert response.get_json()["removed"] == 1
    after = events(client, admin)
    # One old entry gone, and the clearing itself recorded.
    assert after["total"] == before
    assert after["items"][0]["action"] == "audit_cleared"
    assert after["items"][0]["detail"]["scope"] == "older than 90 days"


def test_clearing_everything_leaves_only_its_own_record(client, admin):
    removed = client.post("/api/v1/admin/audit/clear", headers=admin,
                          json={"older_than_days": 0, "password": ADMIN[2],
                                "confirm": "CLEAR AUDIT LOG"}).get_json()
    assert removed["removed"] >= 1 and removed["remaining"] == 1
    remaining = events(client, admin)["items"]
    assert len(remaining) == 1
    assert remaining[0]["action"] == "audit_cleared"
    assert remaining[0]["user"] == ADMIN[0]
    assert remaining[0]["detail"]["scope"] == "everything"


def test_an_unknown_period_is_refused(client, admin):
    response = client.post("/api/v1/admin/audit/clear", headers=admin,
                           json={"older_than_days": 7, "password": ADMIN[2]})
    assert response.status_code == 400
    assert response.get_json()["field"] == "older_than_days"


def test_clinicians_cannot_clear_the_log(client, clinician):
    assert client.post("/api/v1/admin/audit/clear", headers=clinician,
                       json={"older_than_days": 90, "password": CLINICIAN[2]}).status_code == 403
