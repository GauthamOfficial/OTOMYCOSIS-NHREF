"""Daily backtest: the model in service, re-run on every surgeon reviewed case.

The dashboard reports the answers patients were actually given, by whichever
model was in service at the time. A backtest asks a different question: how
does the model in service today do on all the cases the surgeons have
reviewed, old and new? Run every day, the series of results shows whether
performance holds as the case mix, the equipment and the model change, and
raises an alert when it does not. It never changes the model.

Rules that keep the figures honest
- Each case is judged by its latest review.
- Cases a model was retrained on are left out of that model's backtest, or
  it would be marked on its own homework. Images identical to one in the
  original dataset are left out for the same reason.
- Recent cases are reported separately from all time figures, so a recent
  decline is not hidden by a long good history.

Inference needs TensorFlow and takes minutes, so like retraining it runs as a
separate process (scripts/backtest.py). The web service only schedules it.
"""

import csv
import logging
import os
import subprocess
import sys
import threading
from collections import Counter
from datetime import datetime, timedelta

from flask import Blueprint, g, jsonify, request

from . import config, storage
from .auth import client_ip, login_required
from .db import BacktestRun, Examination, ModelVersion, as_utc, db, iso, record, utcnow
from .retraining import JOB_LOG_DIR, process_alive
from .stats import proportion, source_of

log = logging.getLogger("ear_detection")

BACKTEST_SCRIPT = os.path.join(config.BASE_DIR, "scripts", "backtest.py")
MAX_LISTED_MISSES = 25
PROGRESS_EVERY = 25

backtest_bp = Blueprint("backtest", __name__, url_prefix=config.API_PREFIX)


# ---------------- which cases ----------------

def _same_file(path):
    return os.path.normcase(os.path.abspath(path))


def trained_exam_ids(version):
    """Examinations a model version was trained on, or None if unknown.

    Retraining records them in the version summary. Earlier candidates only
    left the list of images beside the model file, so fall back to that.
    """
    summary = version.summary or {}
    if "trained_exam_ids" in summary:
        return set(summary["trained_exam_ids"])
    if not summary.get("reviews_used"):
        return set()
    extra = os.path.join(os.path.dirname(config.absolute(version.model_path)), "extra_rows.csv")
    if not os.path.isfile(extra):
        return None
    with open(extra, newline="", encoding="utf-8") as fh:
        paths = {_same_file(row["path"]) for row in csv.DictReader(fh)}
    return {exam.id for exam in Examination.query.all()
            if _same_file(storage.absolute(exam.image_path)) in paths}


def collect_cases(version, protected_hashes):
    """Reviewed examinations to score, and counts of those left out."""
    trained = trained_exam_ids(version)
    excluded = Counter()
    notes = []
    if trained is None:
        notes.append("Could not tell which reviewed cases model %s was retrained on, so "
                     "its figures may be optimistic." % version.version)
        trained = set()

    cases = []
    for exam in Examination.query.order_by(Examination.id).all():
        review = exam.latest_review
        if review is None:
            continue
        if review.verdict != "other" and not review.label:
            continue
        if exam.id in trained:
            excluded["trained_on"] += 1
            continue
        if exam.image_sha256 in protected_hashes:
            excluded["dataset_copies"] += 1
            continue
        path = storage.absolute(exam.image_path)
        if not os.path.isfile(path):
            excluded["image_missing"] += 1
            continue
        cases.append({
            "exam_id": exam.id,
            "path": path,
            # None for a diagnosis outside the trained classes.
            "truth": review.label,
            "other": review.verdict == "other",
            "examined_at": as_utc(exam.created_at),
            "source": source_of(exam),
            "original_class": exam.predicted_class,
            "original_refer": exam.referral == "refer",
        })
    return cases, dict(excluded), notes


# ---------------- scoring ----------------

def score(cases):
    """Agreement and referral figures for scored cases.

    Defined as on the dashboard: agreement and sensitivity count cases whose
    review names a trained condition, and diagnoses outside the trained
    classes are reported on their own. Each figure is given for the model
    under test and for the answers recorded at the time, so the effect of
    model changes is visible.
    """
    labelled = [c for c in cases if c["truth"]]
    abnormal = [c for c in labelled if c["truth"] != config.NORMAL_CLASS]
    normal = [c for c in labelled if c["truth"] == config.NORMAL_CLASS]
    others = [c for c in cases if c["other"]]
    return {
        "cases": len(cases),
        "agreement": proportion(sum(c["predicted"] == c["truth"] for c in labelled), len(labelled)),
        "sensitivity": proportion(sum(c["refer"] for c in abnormal), len(abnormal)),
        "specificity": proportion(sum(not c["refer"] for c in normal), len(normal)),
        "other_diagnoses_referred": proportion(sum(c["refer"] for c in others), len(others)),
        "missed": sum(not c["refer"] for c in abnormal),
        "at_the_time": {
            "agreement": proportion(sum(c["original_class"] == c["truth"] for c in labelled),
                                    len(labelled)),
            "sensitivity": proportion(sum(c["original_refer"] for c in abnormal), len(abnormal)),
            "specificity": proportion(sum(not c["original_refer"] for c in normal), len(normal)),
        },
    }


def build_results(cases, now, window_days):
    cutoff = now - timedelta(days=window_days)
    recent = [c for c in cases if c["examined_at"] >= cutoff]
    earlier = [c for c in cases if c["examined_at"] < cutoff]
    labelled = [c for c in cases if c["truth"]]

    per_class = []
    for name in sorted({c["truth"] for c in labelled}, key=config.display_name):
        truth = [c for c in labelled if c["truth"] == name]
        per_class.append({
            "class": name,
            "name": config.display_name(name),
            "cases": len(truth),
            "recall": proportion(sum(c["predicted"] == name for c in truth), len(truth)),
        })

    by_source = []
    for source in sorted({c["source"] for c in labelled}):
        rows = [c for c in labelled if c["source"] == source]
        abnormal = [c for c in rows if c["truth"] != config.NORMAL_CLASS]
        by_source.append({
            "source": source,
            "cases": len(rows),
            "agreement": proportion(sum(c["predicted"] == c["truth"] for c in rows), len(rows)),
            "sensitivity": proportion(sum(c["refer"] for c in abnormal), len(abnormal)),
        })

    # Cases where today's model gives a different answer to the one shown at
    # the time, split by whether that moved it towards the surgeon or away.
    changed = [c for c in labelled if c["predicted"] != c["original_class"]]

    missed = sorted((c for c in labelled if c["truth"] != config.NORMAL_CLASS and not c["refer"]),
                    key=lambda c: c["examined_at"], reverse=True)
    return {
        "window_days": window_days,
        "all": score(cases),
        "recent": score(recent),
        "earlier": score(earlier),
        "per_class": per_class,
        "by_source": by_source,
        "changed": {
            "now_agrees": sum(c["predicted"] == c["truth"] for c in changed),
            "now_disagrees": sum(c["original_class"] == c["truth"] for c in changed),
            "total": len(changed),
        },
        "missed_cases": [
            {
                "exam_id": c["exam_id"],
                "examined_at": iso(c["examined_at"]),
                "surgeon": config.display_name(c["truth"]),
                "model": config.display_name(c["predicted"]),
                "abnormal_probability": round(c["abnormal_probability"], 4),
            }
            for c in missed[:MAX_LISTED_MISSES]
        ],
    }


def alerts_for(results):
    """Plain language alerts when performance has fallen."""
    alerts = []
    minimum = config.BACKTEST_MIN_SENSITIVITY
    needed = config.BACKTEST_MIN_CASES
    days = results["window_days"]

    def low_sensitivity(scope, where):
        s = results[scope]["sensitivity"]
        if s["total"] >= needed and s["value"] < minimum:
            alerts.append(
                "Referral sensitivity over %s is %.1f%%: %d of %d abnormal ears would have been "
                "referred, below the %.0f%% alert level." % (
                    where, 100 * s["value"], s["successes"], s["total"], 100 * minimum))
            return True
        return False

    if not low_sensitivity("recent", "the last %d days" % days):
        low_sensitivity("all", "all reviewed cases")

    recent = results["recent"]["agreement"]
    earlier = results["earlier"]["agreement"]
    if (recent["total"] >= needed and earlier["total"] >= needed
            and recent["value"] < earlier["value"] - config.BACKTEST_MAX_AGREEMENT_DROP):
        alerts.append(
            "Agreement with the surgeons over the last %d days is %.1f%%, more than %.0f points "
            "below the %.1f%% on earlier cases. Check for a new device, a change in image "
            "quality or a change in the patients seen." % (
                days, 100 * recent["value"], 100 * config.BACKTEST_MAX_AGREEMENT_DROP,
                100 * earlier["value"]))
    return alerts


def readiness_notes(results):
    """Why an alert check could not yet be made."""
    needed = config.BACKTEST_MIN_CASES
    notes = []
    if results["recent"]["sensitivity"]["total"] < needed:
        notes.append("The sensitivity alert for the last %d days needs %d abnormal reviewed "
                     "cases in that period; there are %d." % (
                         results["window_days"], needed, results["recent"]["sensitivity"]["total"]))
    return notes


def predictor(loaded):
    """Score one stored image with a loaded model (a registry.ActiveModel).

    Uses the same preprocessing, calibration and referral threshold as a
    live examination.
    """
    from . import imaging
    from .analysis import build_result

    meta = loaded.meta

    def predict(path):
        array = imaging.normalize(imaging.open_image(path))
        probabilities, _ = loaded.classifier.predict(array)
        result = build_result(probabilities, meta["classes"], meta["parent_groups"],
                              meta["referral_threshold"])
        return {
            "predicted": result["finding"]["class"],
            "refer": result["referral"]["recommendation"] == "refer",
            "abnormal_probability": result["referral"]["abnormal_probability"],
        }

    return predict


def execute(run, predictor_factory, protected_hashes):
    """Score the run's model on the reviewed cases and store the results.

    ``predictor_factory(version)`` returns a function taking an image path
    and returning the predicted class, the referral decision and the
    abnormal probability. It is only called if there is something to score,
    so a database with no reviews never loads the model.
    """
    version = run.model_version or ModelVersion.query.filter_by(status="active").first()
    run.model_version_id = version.id
    cases, excluded, notes = collect_cases(version, protected_hashes)
    run.message = "Scoring %d reviewed cases." % len(cases)
    db.session.commit()

    scored = []
    if cases:
        predict = predictor_factory(version)
        for done, case in enumerate(cases, 1):
            try:
                case.update(predict(case["path"]))
            except Exception as exc:  # an unreadable image must not end the run
                log.warning("backtest: could not score examination %d: %s", case["exam_id"], exc)
                excluded["unreadable"] = excluded.get("unreadable", 0) + 1
                continue
            scored.append(case)
            if done % PROGRESS_EVERY == 0:
                run.message = "Scored %d of %d reviewed cases." % (done, len(cases))
                db.session.commit()

    results = build_results(scored, utcnow(), config.BACKTEST_WINDOW_DAYS)
    results["excluded"] = excluded
    results["notes"] = notes + readiness_notes(results)
    results["model_version"] = version.version

    previous = (BacktestRun.query
                .filter(BacktestRun.status == "succeeded", BacktestRun.id != run.id)
                .order_by(BacktestRun.started_at.desc()).first())
    if previous is not None:
        before = previous.results or {}
        results["previous"] = {
            "id": previous.id,
            "started_at": iso(previous.started_at),
            "model_version": previous.model_version.version if previous.model_version else None,
            "all": {k: (before.get("all") or {}).get(k) for k in ("cases", "agreement", "sensitivity")},
        }

    alerts = alerts_for(results)
    run.results = results
    run.alerts = alerts
    run.cases = len(scored)
    run.status = "succeeded"
    run.finished_at = utcnow()
    run.message = ("%d reviewed cases scored. %s" % (
        len(scored), "%d alert%s." % (len(alerts), "" if len(alerts) == 1 else "s")
        if alerts else "No alerts."))
    record("backtest_completed", entity="backtest_run", entity_id=run.id,
           detail={"cases": len(scored), "alerts": len(alerts), "model_version": version.version})
    db.session.commit()
    return run


# ---------------- running and scheduling ----------------

def reconcile(run):
    """Mark a run failed if its process died without reporting back."""
    if run is not None and run.status == "running" and run.pid and not process_alive(run.pid):
        db.session.refresh(run)
        if run.status == "running":
            run.status = "failed"
            run.finished_at = utcnow()
            run.message = (run.message or "") + " The backtest process ended unexpectedly."
            db.session.commit()
    return run


def running_run():
    run = BacktestRun.query.filter_by(status="running").order_by(BacktestRun.id.desc()).first()
    reconcile(run)
    return run if run is not None and run.status == "running" else None


def last_slot(now):
    """The most recent scheduled time at or before ``now`` (local time)."""
    slot = now.replace(hour=config.BACKTEST_HOUR, minute=0, second=0, microsecond=0)
    return slot if now >= slot else slot - timedelta(days=1)


def due(now=None):
    """True when no run has started since the most recent scheduled time.

    Failed runs count, so a fault is reported once a day rather than retried
    in a loop.
    """
    now = now or datetime.now().astimezone()
    latest = BacktestRun.query.order_by(BacktestRun.started_at.desc()).first()
    return latest is None or as_utc(latest.started_at) < last_slot(now)


def launch(run):
    """Start the backtest process for a run. Replaced in the tests."""
    os.makedirs(JOB_LOG_DIR, exist_ok=True)
    log_path = os.path.join(JOB_LOG_DIR, "backtest_%d.log" % run.id)
    env = dict(os.environ, PYTHONUNBUFFERED="1", TF_CPP_MIN_LOG_LEVEL="2")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with open(log_path, "w", encoding="utf-8") as fh:
        process = subprocess.Popen(
            [sys.executable, BACKTEST_SCRIPT, "--run-id", str(run.id)],
            cwd=config.BASE_DIR, stdout=fh, stderr=subprocess.STDOUT,
            env=env, creationflags=flags,
        )
    return process.pid


def start(trigger, user=None):
    active = ModelVersion.query.filter_by(status="active").first()
    run = BacktestRun(status="running", trigger=trigger,
                      started_by_id=user.id if user else None,
                      model_version_id=active.id if active else None, message="Starting.")
    db.session.add(run)
    db.session.commit()
    try:
        run.pid = launch(run)
    except Exception as exc:
        run.status = "failed"
        run.finished_at = utcnow()
        run.message = "The backtest could not be started: %s" % exc
    db.session.commit()
    return run


def tick():
    """One scheduler check: start a run if one is due and none is running."""
    if not config.BACKTEST_ENABLED or running_run() is not None or not due():
        return None
    log.info("starting the daily backtest")
    return start("scheduled")


class Scheduler(threading.Thread):
    """Checks every few minutes whether the daily backtest is due.

    Waits a little after start up so the service is serving first, which
    also means a run missed while the computer was off happens soon after
    it is switched on.
    """

    def __init__(self, app, first_delay=120, interval=300):
        super().__init__(name="backtest-scheduler", daemon=True)
        self.app = app
        self.first_delay = first_delay
        self.interval = interval
        self.stopped = threading.Event()

    def run(self):
        delay = self.first_delay
        while not self.stopped.wait(delay):
            delay = self.interval
            try:
                with self.app.app_context():
                    tick()
            except Exception:
                log.exception("backtest scheduler check failed")

    def stop(self):
        self.stopped.set()


def next_run_at(now=None):
    now = now or datetime.now().astimezone()
    if due(now):
        return now
    return last_slot(now) + timedelta(days=1)


# ---------------- API ----------------

@backtest_bp.route("/backtests")
@login_required()
def list_runs():
    limit = min(365, max(1, request.args.get("limit", 60, type=int)))
    runs = BacktestRun.query.order_by(BacktestRun.started_at.desc()).limit(limit).all()
    latest = next((r for r in runs if r.status == "succeeded"), None)
    running = running_run()
    response = jsonify({
        "runs": [r.to_dict() for r in runs],
        "latest": latest.to_dict(full=True) if latest else None,
        "running": running.to_dict() if running else None,
        "schedule": {
            "enabled": config.BACKTEST_ENABLED,
            "hour": config.BACKTEST_HOUR,
            "next_run": iso(next_run_at()) if config.BACKTEST_ENABLED else None,
            "window_days": config.BACKTEST_WINDOW_DAYS,
            "min_sensitivity": config.BACKTEST_MIN_SENSITIVITY,
            "min_cases": config.BACKTEST_MIN_CASES,
            "max_agreement_drop": config.BACKTEST_MAX_AGREEMENT_DROP,
        },
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@backtest_bp.route("/backtests/<int:run_id>")
@login_required()
def get_run(run_id):
    run = db.session.get(BacktestRun, run_id)
    if run is None:
        return jsonify({"error": "Backtest not found."}), 404
    reconcile(run)
    return jsonify({"run": run.to_dict(full=True)})


@backtest_bp.route("/backtests", methods=["POST"])
@login_required("admin")
def run_now():
    if running_run() is not None:
        return jsonify({"error": "A backtest is already running."}), 409
    run = start("manual", g.user)
    record("backtest_started", user=g.user, entity="backtest_run", entity_id=run.id,
           ip=client_ip())
    db.session.commit()
    if run.status == "failed":
        return jsonify({"error": run.message}), 500
    return jsonify({"run": run.to_dict()}), 202
