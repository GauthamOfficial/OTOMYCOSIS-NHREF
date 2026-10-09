"""Login, sessions and role checks.

Two tokens, the standard pattern for a browser application:

- An access token, valid for minutes, returned in the response body and kept
  only in the page's memory. It is sent as ``Authorization: Bearer`` on every
  API call. Nothing persistent, so a reload or a stolen copy is short lived.
- A refresh token, valid for one working shift, stored in an httpOnly cookie
  that page scripts cannot read, scoped to the auth endpoints, SameSite Strict,
  and protected by a double submit CSRF token.

Each token carries the user's ``token_version``. Changing or resetting a
password, or deactivating an account, increments it, which invalidates every
outstanding token for that user at once.
"""

import re
from datetime import timedelta
from functools import wraps

from flask import Blueprint, g, jsonify, request
from flask_jwt_extended import (
    JWTManager, create_access_token, create_refresh_token, get_current_user,
    get_jwt, jwt_required, set_refresh_cookies, unset_jwt_cookies,
    verify_jwt_in_request,
)
from werkzeug.security import check_password_hash, generate_password_hash

from . import config
from .db import User, as_utc, db, record, utcnow

jwt = JWTManager()
auth_bp = Blueprint("auth", __name__, url_prefix=config.API_PREFIX + "/auth")

# Compared against when the username does not exist, so a failed login takes
# the same time whether or not the account is real.
_DUMMY_HASH = generate_password_hash("not-a-real-password-for-timing")


def configure(app):
    app.config.update(
        JWT_SECRET_KEY=config.JWT_SECRET_KEY,
        JWT_ACCESS_TOKEN_EXPIRES=timedelta(minutes=config.ACCESS_TOKEN_MINUTES),
        JWT_REFRESH_TOKEN_EXPIRES=timedelta(hours=config.REFRESH_TOKEN_HOURS),
        JWT_TOKEN_LOCATION=["headers"],
        JWT_REFRESH_COOKIE_PATH=config.API_PREFIX + "/auth",
        JWT_REFRESH_CSRF_COOKIE_PATH="/",
        JWT_COOKIE_CSRF_PROTECT=True,
        JWT_COOKIE_SAMESITE="Strict",
        JWT_COOKIE_SECURE=config.COOKIE_SECURE,
        JWT_SESSION_COOKIE=False,
    )
    jwt.init_app(app)
    app.register_blueprint(auth_bp)


def client_ip():
    """The caller's address, for the audit log.

    A client can send any X-Real-IP header it likes, so it is only believed
    when the service is configured as sitting behind a trusted reverse proxy.
    """
    if config.TRUST_PROXY:
        forwarded = request.headers.get("X-Real-IP")
        if forwarded:
            return forwarded[:64]
    return request.remote_addr


EMAIL = re.compile(r"^[a-z0-9][a-z0-9._%+-]*@[a-z0-9]([a-z0-9-]*[a-z0-9])?"
                   r"(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
MAX_EMAIL_LENGTH = 64


def email_problem(email):
    """A reason the email address cannot be used to sign in, or None.

    Accounts are identified by the person's work or Gmail address, so there
    is no separate username to invent, remember or mistype. The length limit
    is the width of the stored column.
    """
    if not email:
        return "Enter the person's email address."
    if len(email) > MAX_EMAIL_LENGTH:
        return "Email address must be at most %d characters." % MAX_EMAIL_LENGTH
    if not EMAIL.match(email):
        return "Enter a valid email address, for example name@gmail.com."
    return None


def password_problem(password, username=""):
    """A reason the password is unacceptable, or None."""
    if not password or len(password) < config.MIN_PASSWORD_LENGTH:
        return "Password must be at least %d characters." % config.MIN_PASSWORD_LENGTH
    if username and password.lower() == username.lower():
        return "Password must not be the same as the username."
    if password.isdigit() or password.isalpha():
        return "Password must mix letters with numbers or symbols."
    return None


def hash_password(password):
    return generate_password_hash(password)


def issue_tokens(user):
    claims = {"role": user.role, "tv": user.token_version}
    access = create_access_token(identity=str(user.id), additional_claims=claims)
    refresh = create_refresh_token(identity=str(user.id), additional_claims={"tv": user.token_version})
    return access, refresh


def session_response(user, status=200):
    access, refresh = issue_tokens(user)
    response = jsonify({"access_token": access, "user": user.to_dict(),
                        "expires_in": config.ACCESS_TOKEN_MINUTES * 60})
    set_refresh_cookies(response, refresh)
    response.headers["Cache-Control"] = "no-store"
    return response, status


@jwt.user_lookup_loader
def _load_user(_header, data):
    try:
        return db.session.get(User, int(data["sub"]))
    except (KeyError, ValueError, TypeError):
        return None


def _unauthorised(message, code):
    return jsonify({"error": message, "code": code}), 401


@jwt.expired_token_loader
def _expired(_header, _data):
    return _unauthorised("Your session has expired. Please sign in again.", "token_expired")


@jwt.invalid_token_loader
def _invalid(reason):
    return _unauthorised("Sign in required.", "token_invalid")


@jwt.unauthorized_loader
def _missing(reason):
    return _unauthorised("Sign in required.", "token_missing")


def _session_valid(user, claims):
    return user is not None and user.is_active and claims.get("tv") == user.token_version


def login_required(*roles, allow_password_change=False):
    """Require a valid access token, and optionally one of ``roles``.

    Users with a temporary password may only change it until they do.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            user = get_current_user()
            if not _session_valid(user, get_jwt()):
                return _unauthorised("Your session has ended. Please sign in again.", "session_ended")
            if user.must_change_password and not allow_password_change:
                return jsonify({"error": "You must set a new password before continuing.",
                                "code": "password_change_required"}), 403
            if roles and user.role not in roles:
                return jsonify({"error": "You do not have permission to do that.",
                                "code": "forbidden"}), 403
            g.user = user
            return fn(*args, **kwargs)
        return wrapper
    return decorator


@auth_bp.route("/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    # Accounts are identified by email address. Accounts created before that
    # rule still sign in with the name they were given.
    username = str(body.get("email") or body.get("username") or "").strip().lower()
    password = str(body.get("password", ""))
    generic = jsonify({"error": "Incorrect email address or password."}), 401

    user = User.query.filter_by(username=username).first() if username else None
    if user is None:
        check_password_hash(_DUMMY_HASH, password)
        record("login_failed", detail={"username": username[:64]}, ip=client_ip())
        db.session.commit()
        return generic

    now = utcnow()
    locked_until = as_utc(user.locked_until)
    if locked_until and locked_until > now:
        minutes = max(1, int((locked_until - now).total_seconds() // 60) + 1)
        return jsonify({"error": "This account is locked after repeated failed sign ins. "
                                 "Try again in %d minutes or ask an administrator." % minutes,
                        "code": "locked"}), 403

    if not user.is_active or not check_password_hash(user.password_hash, password):
        if user.is_active:
            user.failed_logins += 1
            if user.failed_logins >= config.LOGIN_MAX_FAILURES:
                user.locked_until = now + timedelta(minutes=config.LOGIN_LOCK_MINUTES)
                user.failed_logins = 0
                record("account_locked", user=user, ip=client_ip())
        record("login_failed", user=user, ip=client_ip())
        db.session.commit()
        return generic

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    record("login", user=user, ip=client_ip())
    db.session.commit()
    return session_response(user)


@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True, locations=["cookies"])
def refresh():
    user = get_current_user()
    if not _session_valid(user, get_jwt()):
        response = jsonify({"error": "Your session has ended. Please sign in again.",
                            "code": "session_ended"})
        unset_jwt_cookies(response)
        return response, 401
    access = create_access_token(identity=str(user.id),
                                 additional_claims={"role": user.role, "tv": user.token_version})
    response = jsonify({"access_token": access, "user": user.to_dict(),
                        "expires_in": config.ACCESS_TOKEN_MINUTES * 60})
    response.headers["Cache-Control"] = "no-store"
    return response


@auth_bp.route("/logout", methods=["POST"])
def logout():
    response = jsonify({"ok": True})
    unset_jwt_cookies(response)
    return response


@auth_bp.route("/me")
@login_required(allow_password_change=True)
def me():
    return jsonify({"user": g.user.to_dict()})


@auth_bp.route("/password", methods=["POST"])
@login_required(allow_password_change=True)
def change_password():
    body = request.get_json(silent=True) or {}
    current = str(body.get("current_password", ""))
    new = str(body.get("new_password", ""))
    user = g.user

    if not check_password_hash(user.password_hash, current):
        return jsonify({"error": "Current password is incorrect."}), 400
    problem = password_problem(new, user.username)
    if problem:
        return jsonify({"error": problem}), 400
    if check_password_hash(user.password_hash, new):
        return jsonify({"error": "New password must differ from the current one."}), 400

    user.password_hash = hash_password(new)
    user.must_change_password = False
    user.password_changed_at = utcnow()
    user.token_version += 1
    record("password_changed", user=user, ip=client_ip())
    db.session.commit()
    # Old tokens are now void; hand back a fresh session.
    return session_response(user)
