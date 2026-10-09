"""Database schema.

PostgreSQL in production. Only portable column types are used, so the test
suite can run on SQLite without a database server.

Design notes
- Patients are identified by the hospital record number only. No names,
  dates of birth or contact details are stored; the record number links back
  to the hospital's own patient file.
- Reviews are append only. Changing a verdict adds a row rather than editing
  one, so the history of who said what, and when, is never lost.
- Every examination records the model version that produced it, so results
  remain interpretable after the model is retrained.
"""

from datetime import datetime, timezone

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc)


def as_utc(value):
    """SQLite drops timezone information; treat stored times as UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def iso(value):
    value = as_utc(value)
    return value.isoformat() if value else None


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="clinician")
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=True)
    failed_logins = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime(timezone=True))
    last_login_at = db.Column(db.DateTime(timezone=True))
    password_changed_at = db.Column(db.DateTime(timezone=True))
    # Incremented on password change, reset or deactivation. Tokens carry the
    # value they were issued with, so bumping it signs the user out everywhere.
    token_version = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "role": self.role,
            "is_active": self.is_active,
            "must_change_password": self.must_change_password,
            "last_login_at": iso(self.last_login_at),
            "created_at": iso(self.created_at),
        }


class Patient(db.Model):
    __tablename__ = "patients"

    id = db.Column(db.Integer, primary_key=True)
    record_no = db.Column(db.String(64), unique=True, nullable=False, index=True)
    sex = db.Column(db.String(16), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    examinations = db.relationship("Examination", back_populates="patient",
                                   order_by="Examination.created_at.desc()")


class ModelVersion(db.Model):
    __tablename__ = "model_versions"

    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.String(32), unique=True, nullable=False)
    # active: in service. candidate: trained, awaiting approval.
    # retired: previously active. rejected: candidate that was not approved.
    status = db.Column(db.String(16), nullable=False, default="candidate", index=True)
    model_path = db.Column(db.String(255), nullable=False)
    calibration_path = db.Column(db.String(255))
    evaluation_path = db.Column(db.String(255))
    summary = db.Column(db.JSON)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    approved_at = db.Column(db.DateTime(timezone=True))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    approved_by = db.relationship("User")

    def to_dict(self):
        return {
            "id": self.id,
            "version": self.version,
            "status": self.status,
            "summary": self.summary or {},
            "notes": self.notes,
            "created_at": iso(self.created_at),
            "approved_at": iso(self.approved_at),
            "approved_by": self.approved_by.full_name if self.approved_by else None,
        }


class Examination(db.Model):
    __tablename__ = "examinations"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    ear = db.Column(db.String(8), nullable=False)
    age_years = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.Text)

    # Paths relative to STORAGE_DIR.
    image_path = db.Column(db.String(255), nullable=False)
    model_view_path = db.Column(db.String(255))
    overlay_path = db.Column(db.String(255))
    image_sha256 = db.Column(db.String(64), index=True)
    image_width = db.Column(db.Integer)
    image_height = db.Column(db.Integer)
    # How the image arrived: "upload" or "camera", and for a camera the device
    # name the browser reported. Recorded because accuracy can differ between
    # devices, and the dashboard reports agreement per source.
    image_source = db.Column(db.String(16))
    capture_device = db.Column(db.String(120))

    model_version_id = db.Column(db.Integer, db.ForeignKey("model_versions.id"), nullable=False)
    predicted_class = db.Column(db.String(64), nullable=False, index=True)
    predicted_probability = db.Column(db.Float, nullable=False)
    abnormal_probability = db.Column(db.Float, nullable=False)
    referral = db.Column(db.String(8), nullable=False)
    referral_threshold = db.Column(db.Float, nullable=False)
    low_confidence = db.Column(db.Boolean, nullable=False, default=False)
    margin = db.Column(db.Float)
    entropy_ratio = db.Column(db.Float)
    # Calibrated probability for every class, keyed by class name.
    probabilities = db.Column(db.JSON, nullable=False)
    quality_warnings = db.Column(db.JSON)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    patient = db.relationship("Patient", back_populates="examinations")
    model_version = db.relationship("ModelVersion")
    created_by = db.relationship("User")
    reviews = db.relationship("Review", back_populates="examination",
                              order_by="Review.created_at", cascade="all, delete-orphan")

    @property
    def latest_review(self):
        return self.reviews[-1] if self.reviews else None


class Review(db.Model):
    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True)
    examination_id = db.Column(db.Integer, db.ForeignKey("examinations.id"),
                               nullable=False, index=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # correct: the prediction was right. corrected: the true condition is one
    # of the trained classes. other: a diagnosis outside the trained classes.
    verdict = db.Column(db.String(16), nullable=False)
    true_class = db.Column(db.String(64))
    other_label = db.Column(db.String(200))
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    examination = db.relationship("Examination", back_populates="reviews")
    reviewer = db.relationship("User")

    @property
    def label(self):
        """The trained class the clinician confirmed, or None for 'other'."""
        if self.verdict == "correct":
            return self.examination.predicted_class
        if self.verdict == "corrected":
            return self.true_class
        return None

    def to_dict(self):
        from .config import display_name

        return {
            "id": self.id,
            "verdict": self.verdict,
            "true_class": self.true_class,
            "true_class_name": display_name(self.true_class) if self.true_class else None,
            "label": self.label,
            "label_name": display_name(self.label) if self.label else None,
            "other_label": self.other_label,
            "comment": self.comment,
            "reviewer": self.reviewer.full_name if self.reviewer else None,
            "created_at": iso(self.created_at),
        }


class TrainingJob(db.Model):
    __tablename__ = "training_jobs"

    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(16), nullable=False, default="running", index=True)
    started_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at = db.Column(db.DateTime(timezone=True))
    pid = db.Column(db.Integer)
    log_path = db.Column(db.String(255))
    message = db.Column(db.Text)
    reviews_used = db.Column(db.Integer)
    candidate_id = db.Column(db.Integer, db.ForeignKey("model_versions.id"))

    started_by = db.relationship("User")
    candidate = db.relationship("ModelVersion")

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status,
            "started_by": self.started_by.full_name if self.started_by else None,
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "message": self.message,
            "reviews_used": self.reviews_used,
            "candidate": self.candidate.version if self.candidate else None,
        }


class AuditEvent(db.Model):
    __tablename__ = "audit_events"

    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(48), nullable=False, index=True)
    entity = db.Column(db.String(32))
    entity_id = db.Column(db.String(64))
    detail = db.Column(db.JSON)
    ip = db.Column(db.String(64))

    user = db.relationship("User")

    def to_dict(self):
        return {
            "at": iso(self.at),
            "user": self.user.username if self.user else None,
            "action": self.action,
            "entity": self.entity,
            "entity_id": self.entity_id,
            "detail": self.detail,
            "ip": self.ip,
        }


def record(action, user=None, entity=None, entity_id=None, detail=None, ip=None):
    """Add an audit event to the current session. The caller commits."""
    db.session.add(AuditEvent(
        action=action,
        user_id=user.id if user else None,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
        detail=detail,
        ip=ip,
    ))


def upgrade_schema():
    """Add columns introduced after a database was first created.

    ``create_all`` creates missing tables but never alters existing ones, so a
    database set up with an earlier version would lack newer columns. This
    adds any that are missing, and only ever adds: nothing is renamed,
    changed or dropped, and only columns that may be empty are added, so
    existing rows stay valid. Returns the columns it added.
    """
    from sqlalchemy import inspect, text

    # An open transaction in this process would hold a lock that ALTER TABLE
    # then waits on for ever. End it first.
    db.session.close()

    engine = db.engine
    inspector = inspect(engine)
    added = []
    for table in db.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present or not column.nullable or column.primary_key:
                continue
            column_type = column.type.compile(dialect=engine.dialect)
            preparer = engine.dialect.identifier_preparer
            with engine.begin() as conn:
                if engine.dialect.name == "postgresql":
                    # Another session holding the table (a running service,
                    # an open pgAdmin window) would otherwise make start up
                    # hang silently. Fail with a clear error instead.
                    conn.execute(text("SET LOCAL lock_timeout = '10s'"))
                try:
                    conn.execute(text("ALTER TABLE %s ADD COLUMN %s %s" % (
                        preparer.quote(table.name), preparer.quote(column.name), column_type)))
                except Exception as exc:
                    raise RuntimeError(
                        "Could not upgrade the database table %s: it is in use by another "
                        "program. Close other copies of the system and any database tools, "
                        "then start again. (%s)" % (table.name, exc.__class__.__name__)) from exc
            added.append("%s.%s" % (table.name, column.name))
    return added


def make_db_app(database_url=None):
    """A minimal Flask app with only the database, for command line scripts.

    Lets scripts use the models without loading the classifier.
    """
    from . import config

    app = Flask("ear_detection_cli")
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url or config.DATABASE_URL
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
    db.init_app(app)
    return app
