"""Flask application: the API, the web interface and their wiring.

Patient images and results are stored (see storage.py and db.py) and are only
reachable through authenticated endpoints. Uploaded filenames are never logged
or stored, since clinic filenames often carry patient names or record numbers.
"""

import logging
import time

from flask import Flask, current_app, jsonify, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge

from . import __version__, auth, backtest, config
from .admin import admin_bp
from .auth import login_required
from .db import db, upgrade_schema
from .exams import exams_bp
from .registry import Registry, ensure_seed_version
from .stats import stats_bp

log = logging.getLogger("ear_detection")

MIN_SECRET_LENGTH = 32


def create_app(overrides=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(
        MAX_CONTENT_LENGTH=config.MAX_UPLOAD_BYTES,
        SQLALCHEMY_DATABASE_URI=config.DATABASE_URL,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    )
    auth.configure(app)
    if overrides:
        app.config.update(overrides)

    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        raise RuntimeError("DATABASE_URL is not set. Run 'python scripts/manage.py setup' "
                           "to create the .env file.")
    if len(app.config.get("JWT_SECRET_KEY") or "") < MIN_SECRET_LENGTH:
        raise RuntimeError("JWT_SECRET_KEY must be set to at least %d random characters. "
                           "Run 'python scripts/manage.py setup'." % MIN_SECRET_LENGTH)

    db.init_app(app)
    registry = Registry()
    app.extensions["registry"] = registry

    with app.app_context():
        # Creates any missing tables, then adds columns introduced since an
        # existing database was created. Existing data is never altered.
        db.create_all()
        for column in upgrade_schema():
            log.info("database upgraded: added column %s", column)
        ensure_seed_version()
        started = time.time()
        loaded = registry.load_active()
        log.info("model %s loaded in %.1fs, calibrated=%s",
                 loaded.version, time.time() - started, loaded.meta["calibrated"])

    # A settings file that cannot be read is the kind of fault that otherwise
    # shows up much later as a feature that simply does nothing.
    for problem in config.env_problems():
        log.warning("%s", problem)
    if config.google_enabled():
        log.info("sign in: Google, client ID ending %s. Administrators may also use a password.",
                 config.GOOGLE_CLIENT_ID[-28:])
    else:
        log.info("sign in: passwords for everyone. Set GOOGLE_CLIENT_ID in .env to use "
                 "Sign in with Google.")

    app.register_blueprint(exams_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(backtest.backtest_bp)

    # Re-runs the model on the surgeons' reviewed cases once a day.
    if config.BACKTEST_ENABLED:
        scheduler = backtest.Scheduler(app)
        app.extensions["backtest_scheduler"] = scheduler
        scheduler.start()

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    # ---------------- static interface ----------------

    @app.route("/")
    def index():
        return send_from_directory(config.FRONTEND_DIR, "index.html")

    @app.route("/<path:filename>")
    def static_files(filename):
        return send_from_directory(config.FRONTEND_DIR, filename)

    # ---------------- service information ----------------

    @app.route(config.API_PREFIX + "/health")
    def health():
        active = current_app.extensions["registry"].current()
        return jsonify({
            "status": "ok",
            "version": __version__,
            "model_version": active.version,
            "classes": len(active.meta["classes"]),
            "calibrated": active.meta["calibrated"],
        })

    @app.route(config.API_PREFIX + "/model")
    @login_required()
    def model_card():
        active = current_app.extensions["registry"].current()
        meta = active.meta
        referral = meta["evaluation"].get("referral", {})
        test = meta["evaluation"].get("test", {})
        return jsonify({
            "version": active.version,
            "software_version": __version__,
            "architecture": "MobileNetV2, ImageNet initialised, fine tuned",
            "calibrated": meta["calibrated"],
            "referral_threshold": meta["referral_threshold"],
            "target_sensitivity": meta["calibration"].get("target_sensitivity"),
            "test_set": {
                "images": test.get("images"),
                "sensitivity": referral.get("sensitivity"),
                "sensitivity_ci": referral.get("sensitivity_ci"),
                "specificity": referral.get("specificity"),
                "specificity_ci": referral.get("specificity_ci"),
                "auroc": referral.get("auroc"),
                "macro_f1": test.get("macro_f1"),
            },
            "classes": [
                {"class": c, "name": config.display_name(c), "description": config.description(c)}
                for c in meta["classes"]
            ],
        })

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify({
            "error": "Image exceeds the %d MB limit." % (config.MAX_UPLOAD_BYTES // (1024 * 1024))
        }), 413

    return app
