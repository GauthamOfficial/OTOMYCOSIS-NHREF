"""Administration: users, model versions, retraining, records and the audit log.

Every route here requires the admin role.
"""

import os
import re
import secrets
import shutil
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.security import check_password_hash

from . import config, reporting, retraining, storage
from .auth import client_ip, email_problem, hash_password, login_required, password_problem
from .db import (
    AuditEvent, BacktestRun, Examination, ModelVersion, Patient, Review, TrainingJob, User, as_utc, db,
    iso, record, utcnow,
)

admin_bp = Blueprint("admin", __name__, url_prefix=config.API_PREFIX + "/admin")


def _error(message, status=400, field=None):
    body = {"error": message}
    if field:
        body["field"] = field
    return jsonify(body), status


def _active_admins():
    return User.query.filter_by(role="admin", is_active=True).count()


# ---------------- users ----------------

def _users_with_records():
    """Ids of users who appear anywhere in the clinical or model record.

    Their account cannot be deleted, because deleting it would detach that
    work from the person who did it.
    """
    owners = set()
    for column in (Patient.created_by_id, Examination.created_by_id, Review.reviewer_id,
                   ModelVersion.approved_by_id, TrainingJob.started_by_id,
                   BacktestRun.started_by_id):
        owners.update(i for (i,) in db.session.query(column).filter(column.isnot(None)).distinct())
    return owners


@admin_bp.route("/users")
@login_required("admin")
def list_users():
    users = User.query.order_by(User.full_name).all()
    now = utcnow()
    with_records = _users_with_records()

    def locked(user):
        until = as_utc(user.locked_until)
        return until is not None and until > now

    return jsonify({"items": [
        u.to_dict() | {"locked": locked(u),
                       "deletable": u.id not in with_records and u.id != g.user.id}
        for u in users]})


@admin_bp.route("/users", methods=["POST"])
@login_required("admin")
def create_user():
    body = request.get_json(silent=True) or {}
    username = str(body.get("email") or body.get("username") or "").strip().lower()
    full_name = re.sub(r"\s+", " ", str(body.get("full_name", ""))).strip()
    role = str(body.get("role", "clinician"))
    password = str(body.get("password", ""))

    problem = email_problem(username)
    if problem:
        return _error(problem, field="email")
    if User.query.filter_by(username=username).first():
        return _error("An account already uses that email address.", 409, "email")
    if not 2 <= len(full_name) <= 120:
        return _error("Enter the person's full name.", field="full_name")
    if role not in config.ROLE_CHOICES:
        return _error("Choose a valid role.", field="role")

    # With Google sign in there is normally no password at all. One can still
    # be set, and should be for administrators, as the emergency way in if
    # Google cannot be reached.
    if config.google_enabled() and not password:
        # Unguessable and never shared, so the account can only arrive by Google.
        password, must_change = secrets.token_urlsafe(32), False
    else:
        problem = password_problem(password, username)
        if problem:
            return _error(problem, field="password")
        must_change = True

    user = User(username=username, full_name=full_name, role=role,
                password_hash=hash_password(password), must_change_password=must_change,
                password_set=must_change)
    db.session.add(user)
    db.session.flush()
    record("user_created", user=g.user, entity="user", entity_id=user.id,
           detail={"username": username, "role": role}, ip=client_ip())
    db.session.commit()
    return jsonify({"user": user.to_dict()}), 201


@admin_bp.route("/users/<int:user_id>", methods=["PATCH"])
@login_required("admin")
def update_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        return _error("User not found.", 404)
    body = request.get_json(silent=True) or {}
    changes = {}

    if "email" in body and str(body["email"]).strip().lower() != user.username:
        email = str(body["email"]).strip().lower()
        problem = email_problem(email)
        if problem:
            return _error(problem, field="email")
        if User.query.filter_by(username=email).first():
            return _error("An account already uses that email address.", 409, "email")
        changes["email"] = {"from": user.username, "to": email}
        user.username = email
        # They now sign in with a different address, so end any open session.
        user.token_version += 1

    if "full_name" in body:
        name = re.sub(r"\s+", " ", str(body["full_name"])).strip()
        if not 2 <= len(name) <= 120:
            return _error("Enter the person's full name.", field="full_name")
        user.full_name = changes["full_name"] = name

    if "role" in body and body["role"] != user.role:
        role = str(body["role"])
        if role not in config.ROLE_CHOICES:
            return _error("Choose a valid role.", field="role")
        if user.id == g.user.id:
            return _error("You cannot change your own role.")
        if user.role == "admin" and user.is_active and _active_admins() <= 1:
            return _error("At least one active administrator is required.")
        user.role = changes["role"] = role
        user.token_version += 1

    if "is_active" in body and bool(body["is_active"]) != user.is_active:
        active = bool(body["is_active"])
        if not active and user.id == g.user.id:
            return _error("You cannot deactivate your own account.")
        if not active and user.role == "admin" and _active_admins() <= 1:
            return _error("At least one active administrator is required.")
        user.is_active = changes["is_active"] = active
        user.token_version += 1  # ends any open session at once

    record("user_updated", user=g.user, entity="user", entity_id=user.id,
           detail=changes, ip=client_ip())
    db.session.commit()
    return jsonify({"user": user.to_dict()})


@admin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@login_required("admin")
def reset_password(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        return _error("User not found.", 404)
    password = str((request.get_json(silent=True) or {}).get("password", ""))
    problem = password_problem(password, user.username)
    if problem:
        return _error(problem, field="password")
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.password_set = True
    user.failed_logins = 0
    user.locked_until = None
    user.token_version += 1
    record("password_reset", user=g.user, entity="user", entity_id=user.id, ip=client_ip())
    db.session.commit()
    return jsonify({"user": user.to_dict()})


@admin_bp.route("/users/<int:user_id>", methods=["DELETE"])
@login_required("admin")
def delete_user(user_id):
    """Remove an account that has never been used.

    An account that has screened a patient, reviewed a finding or approved a
    model is never deleted: those records name the person who did the work,
    and that attribution is the point of keeping them. Such an account is
    deactivated instead, which ends its access immediately while the history
    stays readable.
    """
    user = db.session.get(User, user_id)
    if user is None:
        return _error("User not found.", 404)
    if user.id == g.user.id:
        return _error("You cannot delete your own account.")
    if user.role == "admin" and user.is_active and _active_admins() <= 1:
        return _error("At least one active administrator is required.")
    if user.id in _users_with_records():
        return _error("This account has examinations, reviews or approvals recorded against "
                      "it, which must keep naming the person who made them. Deactivate the "
                      "account instead: that ends its access at once.", 409)

    details = {"username": user.username, "full_name": user.full_name, "role": user.role}
    # Their own sign in history stays in the log, no longer pointing at a row
    # that has gone. The deletion below records who they were.
    detached = AuditEvent.query.filter_by(user_id=user.id).update(
        {"user_id": None}, synchronize_session=False)
    details["audit_entries_detached"] = detached
    db.session.delete(user)
    record("user_deleted", user=g.user, entity="user", entity_id=user_id, detail=details,
           ip=client_ip())
    db.session.commit()
    return jsonify({"deleted": details})


# ---------------- models and retraining ----------------

@admin_bp.route("/models")
@login_required("admin")
def list_models():
    versions = ModelVersion.query.order_by(ModelVersion.created_at.desc()).all()
    jobs = TrainingJob.query.order_by(TrainingJob.id.desc()).limit(10).all()
    for job in jobs:
        retraining.reconcile(job)
    return jsonify({
        "active": current_app.extensions["registry"].current().version,
        "versions": [v.to_dict() for v in versions],
        "jobs": [j.to_dict() for j in jobs],
        "readiness": retraining.readiness(),
    })


@admin_bp.route("/retrain", methods=["POST"])
@login_required("admin")
def start_retraining():
    status = retraining.readiness()
    if not status["ready"]:
        return _error(" ".join(status["problems"]), 409)
    job = retraining.start(g.user)
    record("retraining_started", user=g.user, entity="training_job", entity_id=job.id,
           detail={"usable_reviews": status["usable_reviews"]}, ip=client_ip())
    db.session.commit()
    return jsonify({"job": job.to_dict()}), 202


@admin_bp.route("/jobs/<int:job_id>")
@login_required("admin")
def job_status(job_id):
    job = db.session.get(TrainingJob, job_id)
    if job is None:
        return _error("Job not found.", 404)
    retraining.reconcile(job)
    return jsonify({"job": job.to_dict(), "log": retraining.log_tail(job)})


@admin_bp.route("/models/<int:version_id>/approve", methods=["POST"])
@login_required("admin")
def approve_model(version_id):
    candidate = db.session.get(ModelVersion, version_id)
    if candidate is None or candidate.status != "candidate":
        return _error("Only a candidate model can be approved.", 409)
    gate = (candidate.summary or {}).get("gate", {})
    if not gate.get("passed"):
        return _error("This candidate did not pass the validation gate and cannot go live.", 409)

    # Load the new model before touching any records, so a failure leaves the
    # current model serving and the database unchanged.
    try:
        current_app.extensions["registry"].activate(candidate)
    except Exception as exc:
        return _error("The candidate model could not be loaded: %s" % exc, 500)

    for previous in ModelVersion.query.filter_by(status="active").all():
        previous.status = "retired"
    candidate.status = "active"
    candidate.approved_at = utcnow()
    candidate.approved_by_id = g.user.id
    record("model_approved", user=g.user, entity="model_version", entity_id=candidate.id,
           detail={"version": candidate.version}, ip=client_ip())
    db.session.commit()

    evaluation = config.read_json(config.absolute(candidate.evaluation_path), default=None)
    if evaluation:
        reporting.write_reports(evaluation)
    return jsonify({"version": candidate.to_dict()})


@admin_bp.route("/models/<int:version_id>/reject", methods=["POST"])
@login_required("admin")
def reject_model(version_id):
    candidate = db.session.get(ModelVersion, version_id)
    if candidate is None or candidate.status != "candidate":
        return _error("Only a candidate model can be rejected.", 409)
    candidate.status = "rejected"
    record("model_rejected", user=g.user, entity="model_version", entity_id=candidate.id,
           detail={"version": candidate.version}, ip=client_ip())
    db.session.commit()
    return jsonify({"version": candidate.to_dict()})


# ---------------- records ----------------

CLEAR_PHRASE = "DELETE ALL RECORDS"
IMAGE_FOLDERS = ("images", "derived")


def _storage_usage():
    files = size = 0
    for folder in IMAGE_FOLDERS:
        root = storage.absolute(folder)
        for base, _dirs, names in os.walk(root):
            for name in names:
                files += 1
                size += os.path.getsize(os.path.join(base, name))
    return files, size


@admin_bp.route("/records")
@login_required("admin")
def records_summary():
    files, size = _storage_usage()
    return jsonify({
        "patients": Patient.query.count(),
        "examinations": Examination.query.count(),
        "reviews": Review.query.count(),
        "image_files": files,
        "image_bytes": size,
        "confirm_phrase": CLEAR_PHRASE,
    })


@admin_bp.route("/records/clear", methods=["POST"])
@login_required("admin")
def clear_records():
    """Permanently delete every patient, examination, review and image.

    Meant for removing test data before the system is used for real. Needs
    the exact confirmation phrase and the administrator's own password, so an
    unattended signed in screen is not enough. Backtest results go too, since
    they describe the deleted cases. User accounts, model versions and the
    audit log are kept; the audit log records the clearing itself.
    """
    body = request.get_json(silent=True) or {}
    if str(body.get("confirm", "")).strip() != CLEAR_PHRASE:
        return _error('Type "%s" exactly to confirm.' % CLEAR_PHRASE, field="confirm")
    if not check_password_hash(g.user.password_hash, str(body.get("password", ""))):
        return _error("Your password is incorrect.", 403, field="password")

    counts = {
        "patients": Patient.query.count(),
        "examinations": Examination.query.count(),
        "reviews": Review.query.count(),
        "backtests": BacktestRun.query.count(),
    }
    BacktestRun.query.delete()
    Review.query.delete()
    Examination.query.delete()
    Patient.query.delete()
    record("records_cleared", user=g.user, entity="records", detail=counts, ip=client_ip())
    db.session.commit()

    # Database first, files second, as for single deletions.
    for folder in IMAGE_FOLDERS:
        shutil.rmtree(storage.absolute(folder), ignore_errors=True)
    return jsonify({"cleared": counts})


# ---------------- audit ----------------

AUDIT_CLEAR_PHRASE = "CLEAR AUDIT LOG"
AUDIT_CLEAR_CHOICES = (0, 30, 90, 365)


@admin_bp.route("/audit")
@login_required("admin")
def audit_log():
    limit = min(500, max(1, request.args.get("limit", 100, type=int)))
    events = AuditEvent.query.order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).limit(limit).all()
    oldest = AuditEvent.query.order_by(AuditEvent.at).first()
    return jsonify({
        "items": [e.to_dict() for e in events],
        "total": AuditEvent.query.count(),
        "oldest": iso(oldest.at) if oldest else None,
        "confirm_phrase": AUDIT_CLEAR_PHRASE,
        "keep_choices": list(AUDIT_CLEAR_CHOICES),
    })


@admin_bp.route("/audit/clear", methods=["POST"])
@login_required("admin")
def clear_audit_log():
    """Delete audit entries, either all of them or those past a given age.

    The log is the record of who did what, so clearing it is itself recorded:
    the entry describing this deletion is written afterwards and survives it.
    Removing everything also needs the confirmation phrase, since nothing
    before this moment can be reconstructed afterwards.
    """
    body = request.get_json(silent=True) or {}
    try:
        older_than_days = int(body.get("older_than_days", 0))
    except (TypeError, ValueError):
        older_than_days = -1
    if older_than_days not in AUDIT_CLEAR_CHOICES:
        return _error("Choose how much of the log to clear.", field="older_than_days")
    if not check_password_hash(g.user.password_hash, str(body.get("password", ""))):
        return _error("Your password is incorrect.", 403, field="password")
    if older_than_days == 0 and str(body.get("confirm", "")).strip() != AUDIT_CLEAR_PHRASE:
        return _error('Type "%s" exactly to clear the whole log.' % AUDIT_CLEAR_PHRASE,
                      field="confirm")

    query = AuditEvent.query
    detail = {"scope": "everything"}
    if older_than_days:
        cutoff = utcnow() - timedelta(days=older_than_days)
        query = query.filter(AuditEvent.at < cutoff)
        detail = {"scope": "older than %d days" % older_than_days, "before": iso(cutoff)}

    detail["removed"] = query.count()
    query.delete(synchronize_session=False)
    # Written after the deletion, so this entry is never removed by it.
    record("audit_cleared", user=g.user, entity="audit", detail=detail, ip=client_ip())
    db.session.commit()
    return jsonify({"removed": detail["removed"], "remaining": AuditEvent.query.count()})
