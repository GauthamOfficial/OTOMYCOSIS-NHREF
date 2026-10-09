"""Real world performance, measured against the surgeons' reviews.

The validation report describes the model on retrospective public data. This
describes it on this clinic's patients and equipment, with each reviewing
surgeon's verdict taken as the truth. It is the evidence that matters before
relying on the system, and it grows with every review.
"""

from collections import Counter

from flask import Blueprint, current_app, jsonify

from . import config
from .analysis import wilson
from .auth import login_required
from .db import Examination

stats_bp = Blueprint("stats", __name__, url_prefix=config.API_PREFIX)


def _proportion(successes, total):
    low, high = wilson(successes, total)
    return {
        "value": successes / total if total else None,
        "ci": [low, high],
        "successes": successes,
        "total": total,
    }


@stats_bp.route("/stats")
@login_required()
def statistics():
    exams = Examination.query.all()
    classes = current_app.extensions["registry"].current().meta["classes"]

    reviewed = [e for e in exams if e.latest_review is not None]
    labelled = [(e, e.latest_review.label) for e in reviewed if e.latest_review.label]
    others = [e.latest_review.other_label for e in reviewed if e.latest_review.verdict == "other"]

    agree = sum(1 for e, label in labelled if label == e.predicted_class)

    # Referral judged against the surgeon: an ear needed referral if the
    # confirmed condition is anything other than normal.
    tp = sum(1 for e, l in labelled if l != config.NORMAL_CLASS and e.referral == "refer")
    fn = sum(1 for e, l in labelled if l != config.NORMAL_CLASS and e.referral != "refer")
    tn = sum(1 for e, l in labelled if l == config.NORMAL_CLASS and e.referral != "refer")
    fp = sum(1 for e, l in labelled if l == config.NORMAL_CLASS and e.referral == "refer")
    # Diagnoses outside the trained classes are still abnormal ears.
    other_referred = sum(1 for e in reviewed
                         if e.latest_review.verdict == "other" and e.referral == "refer")

    per_class = []
    for c in classes:
        truth = [e for e, l in labelled if l == c]
        predicted = [e for e, l in labelled if e.predicted_class == c]
        per_class.append({
            "class": c,
            "name": config.display_name(c),
            "cases": len(truth),
            "recall": _proportion(sum(1 for e in truth if e.predicted_class == c), len(truth)),
            "precision": _proportion(sum(1 for e, l in labelled
                                         if e.predicted_class == c and l == c), len(predicted)),
        })

    # Accuracy can differ between devices (the training data already showed
    # source dependence), so agreement is reported per image source.
    def source_of(exam):
        if (exam.image_source or "upload") == "camera":
            return "Camera: " + (exam.capture_device or "unnamed device")
        return "Uploaded file"

    by_source = []
    for source in sorted({source_of(e) for e, _ in labelled}):
        cases = [(e, l) for e, l in labelled if source_of(e) == source]
        abnormal = [(e, l) for e, l in cases if l != config.NORMAL_CLASS]
        by_source.append({
            "source": source,
            "cases": len(cases),
            "agreement": _proportion(sum(1 for e, l in cases if l == e.predicted_class), len(cases)),
            "sensitivity": _proportion(sum(1 for e, l in abnormal if e.referral == "refer"), len(abnormal)),
        })

    corrections = Counter((e.predicted_class, l) for e, l in labelled if l != e.predicted_class)
    other_counts = Counter(o.strip().lower() for o in others if o)
    versions = Counter(e.model_version.version for e in exams if e.model_version)

    response = jsonify({
        "totals": {
            "examinations": len(exams),
            "reviewed": len(reviewed),
            "unreviewed": len(exams) - len(reviewed),
            "referred": sum(1 for e in exams if e.referral == "refer"),
        },
        "agreement": _proportion(agree, len(labelled)),
        "referral": {
            "sensitivity": _proportion(tp, tp + fn),
            "specificity": _proportion(tn, tn + fp),
            "missed": fn,
            "unnecessary": fp,
            "other_diagnoses_referred": _proportion(other_referred, len(others)),
        },
        "per_class": per_class,
        "by_source": by_source,
        "corrections": [
            {"predicted": config.display_name(p), "actual": config.display_name(a), "count": n}
            for (p, a), n in corrections.most_common(10)
        ],
        "other_diagnoses": [{"label": label, "count": n} for label, n in other_counts.most_common(20)],
        "by_model_version": dict(versions),
    })
    response.headers["Cache-Control"] = "no-store"
    return response
