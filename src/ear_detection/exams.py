"""Examinations: analyse and record an image, list history, record reviews."""

import os
import re

from flask import Blueprint, current_app, g, jsonify, request, send_file
from PIL import UnidentifiedImageError
from sqlalchemy import func, or_

from . import analysis, config, imaging, storage
from .auth import client_ip, login_required
from .db import Examination, Patient, Review, db, iso, record

exams_bp = Blueprint("exams", __name__, url_prefix=config.API_PREFIX)

RECORD_NO = re.compile(r"^[A-Z0-9][A-Z0-9/_.\-]{0,63}$")
VERDICTS = ("correct", "corrected", "other")
IMAGE_SOURCES = ("upload", "camera")


def registry():
    return current_app.extensions["registry"]


def normalise_record_no(value):
    return re.sub(r"\s+", "", str(value or "")).upper()


def bad_request(message, field=None):
    body = {"error": message}
    if field:
        body["field"] = field
    return jsonify(body), 400


# ---------------- serialisation ----------------

def review_status(exam):
    review = exam.latest_review
    if review is None:
        return "unreviewed"
    return {"correct": "confirmed", "corrected": "corrected", "other": "other"}[review.verdict]


def exam_summary(exam):
    review = exam.latest_review
    return {
        "id": exam.id,
        "created_at": iso(exam.created_at),
        "record_no": exam.patient.record_no,
        "sex": exam.patient.sex,
        "age_years": exam.age_years,
        "ear": exam.ear,
        "predicted_class": exam.predicted_class,
        "predicted_name": config.display_name(exam.predicted_class),
        "predicted_probability": exam.predicted_probability,
        "referral": exam.referral,
        "abnormal_probability": exam.abnormal_probability,
        "low_confidence": exam.low_confidence,
        "model_version": exam.model_version.version if exam.model_version else None,
        "clinician": exam.created_by.full_name if exam.created_by else None,
        "image_source": exam.image_source or "upload",
        "capture_device": exam.capture_device,
        "status": review_status(exam),
        "review": review.to_dict() if review else None,
    }


def exam_detail(exam):
    meta = registry().current().meta
    result = analysis.build_result(exam.probabilities, meta["classes"],
                                   meta["parent_groups"], exam.referral_threshold)
    result["quality"] = {"warnings": exam.quality_warnings or []}
    detail = exam_summary(exam)
    detail.update({
        "notes": exam.notes,
        "result": result,
        "reviews": [r.to_dict() for r in exam.reviews],
        "images": {
            kind: "%s/examinations/%d/image/%s" % (config.API_PREFIX, exam.id, kind)
            for kind in ("original", "view", "overlay")
        },
        "image_size": [exam.image_width, exam.image_height],
    })
    return detail


# ---------------- patients ----------------

@exams_bp.route("/patients/<path:record_no>")
@login_required()
def patient_lookup(record_no):
    record_no = normalise_record_no(record_no)
    patient = Patient.query.filter_by(record_no=record_no).first()
    if patient is None:
        return jsonify({"exists": False, "record_no": record_no})
    last = patient.examinations[0] if patient.examinations else None
    return jsonify({
        "exists": True,
        "record_no": patient.record_no,
        "sex": patient.sex,
        "examinations": len(patient.examinations),
        "last_examined": iso(last.created_at) if last else None,
    })


# ---------------- examinations ----------------

@exams_bp.route("/examinations", methods=["POST"])
@login_required()
def create_examination():
    form = request.form
    record_no = normalise_record_no(form.get("record_no"))
    if not RECORD_NO.match(record_no):
        return bad_request("Enter the hospital record number (letters, digits, / _ . -).", "record_no")
    try:
        age = int(form.get("age", ""))
    except ValueError:
        return bad_request("Enter the patient's age in years.", "age")
    if not 0 <= age <= 120:
        return bad_request("Age must be between 0 and 120.", "age")
    sex = str(form.get("sex", "")).lower()
    if sex not in config.SEX_CHOICES:
        return bad_request("Select the patient's sex.", "sex")
    ear = str(form.get("ear", "")).lower()
    if ear not in config.EAR_CHOICES:
        return bad_request("Select which ear was examined.", "ear")
    notes = str(form.get("notes", "")).strip()[:2000] or None
    image_source = str(form.get("image_source", "upload")).lower() or "upload"
    if image_source not in IMAGE_SOURCES:
        return bad_request("Unknown image source.", "image_source")
    # The device name as the browser reports it, e.g. "USB Video Endoscope".
    capture_device = re.sub(r"\s+", " ", str(form.get("capture_device", ""))).strip()[:120] or None
    if image_source != "camera":
        capture_device = None

    upload = request.files.get("file")
    if upload is None or upload.filename == "":
        return bad_request("No image was uploaded.", "file")
    extension = os.path.splitext(upload.filename)[1].lower()
    if extension not in config.ALLOWED_EXTENSIONS:
        return bad_request("Unsupported file type. Use JPG, PNG, BMP, TIFF or WebP.", "file")

    raw = upload.read()
    try:
        image = imaging.open_image(raw)
    except (UnidentifiedImageError, OSError, ValueError):
        return bad_request("The file could not be read as an image.", "file")
    if min(image.size) < config.MIN_IMAGE_SIDE:
        return bad_request("Image is too small (%dx%d). Use at least %d pixels on the short side."
                           % (image.size[0], image.size[1], config.MIN_IMAGE_SIDE), "file")

    patient = Patient.query.filter_by(record_no=record_no).first()
    if patient is not None and patient.sex != sex:
        # Most likely a mistyped record number; refuse rather than mix patients.
        return jsonify({"error": "Record %s is registered as %s. Check the record number."
                                 % (record_no, patient.sex),
                        "field": "sex"}), 409

    # Fit the image to the storage limit first, then analyse what is kept, so
    # the stored image is exactly the one the result was based on.
    upload_sha256 = storage.sha256(raw)
    stored, extension, image = storage.fit_for_storage(raw, extension, image)
    del raw

    active = registry().current()
    result, extras = analysis.analyse(active.classifier, active.meta, image)
    width, height = image.size
    del image

    paths = storage.save_examination(stored, extension, extras["model_view"], extras["overlay"],
                                     upload_sha256)
    duplicate = Examination.query.filter_by(image_sha256=paths["image_sha256"]).first()

    try:
        if patient is None:
            patient = Patient(record_no=record_no, sex=sex, created_by_id=g.user.id)
            db.session.add(patient)
            db.session.flush()

        finding, referral = result["finding"], result["referral"]
        exam = Examination(
            patient_id=patient.id, ear=ear, age_years=age, notes=notes,
            image_path=paths["image_path"], model_view_path=paths["model_view_path"],
            overlay_path=paths["overlay_path"], image_sha256=paths["image_sha256"],
            image_width=width, image_height=height,
            image_source=image_source, capture_device=capture_device,
            model_version_id=active.version_id,
            predicted_class=finding["class"], predicted_probability=finding["probability"],
            abnormal_probability=referral["abnormal_probability"],
            referral=referral["recommendation"], referral_threshold=referral["threshold"],
            low_confidence=finding["low_confidence"], margin=finding["margin"],
            entropy_ratio=finding["entropy_ratio"],
            probabilities=extras["probabilities"], quality_warnings=extras["warnings"],
            created_by_id=g.user.id,
        )
        db.session.add(exam)
        db.session.flush()
        record("examination_created", user=g.user, entity="examination", entity_id=exam.id,
               detail={"record_no": record_no, "model_version": active.version}, ip=client_ip())
        db.session.commit()
    except Exception:
        db.session.rollback()
        storage.remove([paths["image_path"], paths["model_view_path"], paths["overlay_path"]])
        raise

    detail = exam_detail(exam)
    if duplicate is not None:
        detail["duplicate_of"] = {"id": duplicate.id, "created_at": iso(duplicate.created_at)}
    response = jsonify(detail)
    response.headers["Cache-Control"] = "no-store"
    return response, 201


def _latest_reviews():
    latest = (db.session.query(Review.examination_id, func.max(Review.id).label("review_id"))
              .group_by(Review.examination_id).subquery())
    return latest


@exams_bp.route("/examinations")
@login_required()
def list_examinations():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 25, type=int)))
    status = request.args.get("status", "all")
    query_text = normalise_record_no(request.args.get("q", ""))

    latest = _latest_reviews()
    query = (Examination.query.join(Patient)
             .outerjoin(latest, latest.c.examination_id == Examination.id)
             .outerjoin(Review, Review.id == latest.c.review_id))

    if query_text:
        query = query.filter(Patient.record_no.contains(query_text))
    if status == "unreviewed":
        query = query.filter(latest.c.review_id.is_(None))
    elif status == "reviewed":
        query = query.filter(latest.c.review_id.isnot(None))
    elif status == "disagreement":
        query = query.filter(or_(Review.verdict == "corrected", Review.verdict == "other"))
    elif status == "refer":
        query = query.filter(Examination.referral == "refer")

    total = query.count()
    rows = (query.order_by(Examination.created_at.desc(), Examination.id.desc())
            .offset((page - 1) * per_page).limit(per_page).all())

    response = jsonify({
        "items": [exam_summary(e) for e in rows],
        "page": page, "per_page": per_page, "total": total,
        "pages": max(1, -(-total // per_page)),
    })
    response.headers["Cache-Control"] = "no-store"
    return response


def _get_exam(exam_id):
    exam = db.session.get(Examination, exam_id)
    if exam is None:
        return None, (jsonify({"error": "Examination not found."}), 404)
    return exam, None


@exams_bp.route("/examinations/<int:exam_id>")
@login_required()
def get_examination(exam_id):
    exam, error = _get_exam(exam_id)
    if error:
        return error
    response = jsonify(exam_detail(exam))
    response.headers["Cache-Control"] = "no-store"
    return response


@exams_bp.route("/examinations/<int:exam_id>", methods=["DELETE"])
@login_required("admin")
def delete_examination(exam_id):
    """Permanently delete one examination, its reviews and its images.

    Intended for mistakes such as the wrong patient or the wrong image. A
    reason is required and, with what was deleted, is kept in the audit log.
    The patient entry is removed too once it has no examinations left.
    """
    exam, error = _get_exam(exam_id)
    if error:
        return error
    reason = re.sub(r"\s+", " ", str((request.get_json(silent=True) or {}).get("reason", ""))).strip()
    if not 3 <= len(reason) <= 300:
        return bad_request("Give a reason for deleting this examination (3 to 300 characters).", "reason")

    patient = exam.patient
    files = [exam.image_path, exam.model_view_path, exam.overlay_path]
    detail = {
        "record_no": patient.record_no,
        "examined_at": iso(exam.created_at),
        "predicted_class": exam.predicted_class,
        "reviews_deleted": len(exam.reviews),
        "reason": reason,
    }

    db.session.delete(exam)
    db.session.flush()
    patient_removed = Examination.query.filter_by(patient_id=patient.id).first() is None
    if patient_removed:
        db.session.delete(patient)
    detail["patient_removed"] = patient_removed
    record("examination_deleted", user=g.user, entity="examination", entity_id=exam_id,
           detail=detail, ip=client_ip())
    db.session.commit()

    # Files go only after the database change is committed, so a failure can
    # never leave a record pointing at images that no longer exist.
    storage.remove(files)
    return jsonify({"deleted": exam_id, "patient_removed": patient_removed})


@exams_bp.route("/examinations/<int:exam_id>/image/<kind>")
@login_required()
def examination_image(exam_id, kind):
    exam, error = _get_exam(exam_id)
    if error:
        return error
    relative = {"original": exam.image_path, "view": exam.model_view_path,
                "overlay": exam.overlay_path}.get(kind)
    if not relative:
        return jsonify({"error": "Unknown image kind."}), 404
    try:
        path = storage.absolute(relative)
    except ValueError:
        return jsonify({"error": "Image not available."}), 404
    if not os.path.isfile(path):
        return jsonify({"error": "Image file is missing from storage."}), 404
    response = send_file(path, mimetype=storage.mime_type(relative), max_age=0)
    response.headers["Cache-Control"] = "no-store"
    return response


@exams_bp.route("/examinations/<int:exam_id>/review", methods=["POST"])
@login_required()
def review_examination(exam_id):
    exam, error = _get_exam(exam_id)
    if error:
        return error

    body = request.get_json(silent=True) or {}
    verdict = str(body.get("verdict", "")).lower()
    if verdict not in VERDICTS:
        return bad_request("Choose whether the prediction was correct.", "verdict")

    classes = registry().current().meta["classes"]
    true_class = other_label = None
    if verdict == "corrected":
        true_class = str(body.get("true_class", ""))
        if true_class not in classes:
            return bad_request("Select the correct condition.", "true_class")
        if true_class == exam.predicted_class:
            verdict, true_class = "correct", None
    elif verdict == "other":
        other_label = re.sub(r"\s+", " ", str(body.get("other_label", ""))).strip()
        if not 2 <= len(other_label) <= 200:
            return bad_request("Type the diagnosis (2 to 200 characters).", "other_label")

    comment = str(body.get("comment", "")).strip()[:1000] or None

    review = Review(examination_id=exam.id, reviewer_id=g.user.id, verdict=verdict,
                    true_class=true_class, other_label=other_label, comment=comment)
    db.session.add(review)
    db.session.flush()
    record("examination_reviewed", user=g.user, entity="examination", entity_id=exam.id,
           detail={"verdict": verdict, "true_class": true_class, "other_label": other_label},
           ip=client_ip())
    db.session.commit()
    db.session.refresh(exam)

    response = jsonify(exam_detail(exam))
    response.headers["Cache-Control"] = "no-store"
    return response
