"""The daily backtest against the surgeons' reviews."""

import csv
import os
from datetime import datetime, timedelta, timezone

import pytest

from conftest import ADMIN, _TMP, examination, image_bytes


@pytest.fixture
def clean(app):
    """Start from an empty record, since the database is shared by the tests."""
    from ear_detection.db import BacktestRun, Examination, Patient, Review, db

    with app.app_context():
        for model in (BacktestRun, Review, Examination, Patient):
            model.query.delete()
        db.session.commit()
    yield
    with app.app_context():
        BacktestRun.query.delete()
        db.session.commit()


def reviewed(client, headers, record_no, verdict, **fields):
    # A different image per record, so content hashes differ.
    rotation = 1 + sum(map(ord, record_no)) % 359
    exam = examination(client, headers, record_no=record_no,
                       data=image_bytes(rotate=rotation)).get_json()
    response = client.post("/api/v1/examinations/%d/review" % exam["id"],
                           json=dict(verdict=verdict, **fields), headers=headers)
    assert response.status_code in (200, 201), response.get_json()
    return exam


def new_run(app):
    from ear_detection.db import BacktestRun, ModelVersion, db

    active = ModelVersion.query.filter_by(status="active").first()
    run = BacktestRun(status="running", trigger="manual", model_version_id=active.id)
    db.session.add(run)
    db.session.commit()
    return run


def fixed(predicted, refer):
    """A stand in model giving the same answer for every image."""
    return lambda version: (lambda path: {"predicted": predicted, "refer": refer,
                                          "abnormal_probability": 0.9 if refer else 0.1})


# ---------------- scoring ----------------

def case(truth, predicted, refer, days_ago=0, original=None, original_refer=None, other=False):
    return {
        "exam_id": 1, "truth": truth, "other": other, "predicted": predicted, "refer": refer,
        "abnormal_probability": 0.5, "source": "Uploaded file",
        "examined_at": datetime.now(timezone.utc) - timedelta(days=days_ago),
        "original_class": original or predicted,
        "original_refer": refer if original_refer is None else original_refer,
    }


def test_figures_follow_the_dashboard_definitions():
    from ear_detection.backtest import build_results

    cases = [
        case("acute_otitis_media", "acute_otitis_media", True),
        case("otitis_externa", "normal", False, original="otitis_externa", original_refer=True),
        case("normal", "normal", False),
        case("normal", "otitis_externa", True),
        case(None, "normal", True, other=True),
    ]
    results = build_results(cases, datetime.now(timezone.utc), 30)
    everything = results["all"]
    assert everything["cases"] == 5
    assert everything["agreement"]["successes"] == 2 and everything["agreement"]["total"] == 4
    assert everything["sensitivity"]["successes"] == 1 and everything["sensitivity"]["total"] == 2
    assert everything["specificity"]["successes"] == 1 and everything["specificity"]["total"] == 2
    assert everything["other_diagnoses_referred"]["successes"] == 1
    assert everything["missed"] == 1
    # The answer given at the time was right on the otitis externa case.
    assert everything["at_the_time"]["agreement"]["successes"] == 3
    assert everything["at_the_time"]["sensitivity"]["successes"] == 2
    assert results["changed"] == {"now_agrees": 0, "now_disagrees": 1, "total": 1}
    assert [m["surgeon"] for m in results["missed_cases"]] == ["Otitis Externa"]


def test_recent_and_earlier_cases_are_reported_separately():
    from ear_detection.backtest import build_results

    cases = [case("normal", "normal", False, days_ago=3),
             case("normal", "normal", False, days_ago=90),
             case("normal", "normal", False, days_ago=200)]
    results = build_results(cases, datetime.now(timezone.utc), 30)
    assert (results["all"]["cases"], results["recent"]["cases"], results["earlier"]["cases"]) == (3, 1, 2)


def test_low_recent_sensitivity_raises_an_alert():
    from ear_detection import config
    from ear_detection.backtest import alerts_for, build_results

    needed = config.BACKTEST_MIN_CASES
    missed = needed // 4
    cases = ([case("acute_otitis_media", "acute_otitis_media", True, days_ago=2)] * (needed - missed)
             + [case("acute_otitis_media", "normal", False, days_ago=2)] * missed)
    alerts = alerts_for(build_results(cases, datetime.now(timezone.utc), 30))
    assert len(alerts) == 1
    assert "last 30 days" in alerts[0] and "below the 90% alert level" in alerts[0]


def test_too_few_cases_never_alert():
    from ear_detection.backtest import alerts_for, build_results, readiness_notes

    cases = [case("acute_otitis_media", "normal", False, days_ago=2)] * 5
    results = build_results(cases, datetime.now(timezone.utc), 30)
    assert alerts_for(results) == []
    assert "needs" in readiness_notes(results)[0]


def test_a_recent_fall_in_agreement_raises_an_alert():
    from ear_detection import config
    from ear_detection.backtest import alerts_for, build_results

    n = config.BACKTEST_MIN_CASES
    earlier = [case("normal", "normal", False, days_ago=100)] * n
    recent = ([case("normal", "normal", False, days_ago=1)] * (n // 2)
              + [case("normal", "otitis_externa", False, days_ago=1)] * (n - n // 2))
    alerts = alerts_for(build_results(earlier + recent, datetime.now(timezone.utc), 30))
    assert len(alerts) == 1 and "Agreement with the surgeons" in alerts[0]


# ---------------- runs on the stored cases ----------------

def test_backtest_scores_reviewed_cases_with_the_real_model(app, client, clinician, clean):
    from ear_detection import backtest

    with app.app_context():
        normal = reviewed(client, clinician, "NHR-BT-1", "corrected", true_class="normal")
        aom = reviewed(client, clinician, "NHR-BT-2", "corrected", true_class="acute_otitis_media")
        reviewed(client, clinician, "NHR-BT-3", "other", other_label="Exostosis")
        examination(client, clinician, record_no="NHR-BT-4")  # never reviewed: left out

        loaded = app.extensions["registry"].current()
        run = backtest.execute(new_run(app), lambda version: backtest.predictor(loaded), set())

        assert run.status == "succeeded"
        assert run.cases == 3
        results = run.results
        assert results["all"]["agreement"]["total"] == 2
        # The real model sees the same image it saw at the time, so it repeats
        # the answer that was stored with the examination.
        assert results["changed"]["total"] == 0
        assert results["all"]["at_the_time"] == {
            key: results["all"][key] for key in ("agreement", "sensitivity", "specificity")}
        missed_ids = {m["exam_id"] for m in results["missed_cases"]}
        assert missed_ids <= {aom["id"]}
        assert normal["id"] not in missed_ids


def test_a_run_with_nothing_reviewed_never_loads_the_model(app, clean):
    from ear_detection import backtest

    def must_not_load(version):
        raise AssertionError("the model was loaded")

    with app.app_context():
        run = backtest.execute(new_run(app), must_not_load, set())
        assert run.status == "succeeded" and run.cases == 0
        assert run.alerts == []


def test_cases_a_model_was_retrained_on_are_left_out(app, client, clinician, clean):
    from ear_detection import backtest
    from ear_detection.db import ModelVersion, db, Examination
    from ear_detection import storage

    with app.app_context():
        first = reviewed(client, clinician, "NHR-BT-T1", "corrected", true_class="normal")
        second = reviewed(client, clinician, "NHR-BT-T2", "corrected", true_class="normal")

        recorded = ModelVersion(version="8.1.0", status="retired", model_path="models/x.keras",
                                summary={"reviews_used": 1, "trained_exam_ids": [first["id"]]})
        # An older candidate that only left its image list beside the model.
        folder = os.path.join(_TMP, "candidate-8.2.0")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "extra_rows.csv"), "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["path", "label"])
            writer.writeheader()
            writer.writerow({"path": storage.absolute(db.session.get(Examination, second["id"]).image_path),
                             "label": "normal"})
        listed = ModelVersion(version="8.2.0", status="retired",
                              model_path=os.path.join(folder, "model.keras"),
                              summary={"reviews_used": 1})
        unknown = ModelVersion(version="8.3.0", status="retired",
                               model_path=os.path.join(_TMP, "nowhere", "model.keras"),
                               summary={"reviews_used": 4})
        db.session.add_all([recorded, listed, unknown])
        db.session.commit()

        cases, excluded, _ = backtest.collect_cases(recorded, set())
        assert [c["exam_id"] for c in cases] == [second["id"]]
        assert excluded == {"trained_on": 1}

        cases, excluded, _ = backtest.collect_cases(listed, set())
        assert [c["exam_id"] for c in cases] == [first["id"]]

        cases, _, notes = backtest.collect_cases(unknown, set())
        assert len(cases) == 2 and "optimistic" in notes[0]

        protected = {db.session.get(Examination, first["id"]).image_sha256}
        _, excluded, _ = backtest.collect_cases(ModelVersion.query.filter_by(status="active").first(),
                                                protected)
        assert excluded == {"dataset_copies": 1}

        # The list of trained cases is never sent to the browser.
        assert "trained_exam_ids" not in recorded.to_dict()["summary"]

        for version in (recorded, listed, unknown):
            db.session.delete(version)
        db.session.commit()


# ---------------- schedule ----------------

def test_schedule_runs_once_a_day_after_the_set_hour(app, clean, monkeypatch):
    from ear_detection import backtest, config
    from ear_detection.db import BacktestRun, db

    monkeypatch.setattr(config, "BACKTEST_HOUR", 2)
    local = datetime.now().astimezone().tzinfo
    before = datetime(2026, 10, 9, 1, 30, tzinfo=local)
    after = datetime(2026, 10, 9, 2, 5, tzinfo=local)
    assert backtest.last_slot(before) == datetime(2026, 10, 8, 2, 0, tzinfo=local)
    assert backtest.last_slot(after) == datetime(2026, 10, 9, 2, 0, tzinfo=local)

    with app.app_context():
        assert backtest.due(after)  # never run
        # Stored as UTC, as utcnow() does; SQLite keeps no time zone.
        started = datetime(2026, 10, 8, 2, 3, tzinfo=local).astimezone(timezone.utc)
        db.session.add(BacktestRun(status="succeeded", trigger="scheduled", started_at=started))
        db.session.commit()
        assert not backtest.due(before)  # yesterday's run covers until 02:00 today
        assert backtest.due(after)
        assert backtest.next_run_at(datetime(2026, 10, 8, 12, 0, tzinfo=local)) == \
            datetime(2026, 10, 9, 2, 0, tzinfo=local)


def test_scheduler_starts_one_run_at_a_time(app, clean, monkeypatch):
    from ear_detection import backtest, config

    monkeypatch.setattr(config, "BACKTEST_ENABLED", True)
    monkeypatch.setattr(backtest, "launch", lambda run: os.getpid())
    with app.app_context():
        run = backtest.tick()
        assert run.trigger == "scheduled" and run.status == "running"
        assert backtest.tick() is None  # still running
        run.status = "succeeded"
        from ear_detection.db import db
        db.session.commit()
        assert backtest.tick() is None  # already ran since the last scheduled time

    monkeypatch.setattr(config, "BACKTEST_ENABLED", False)
    with app.app_context():
        backtest.BacktestRun.query.delete()
        assert backtest.tick() is None


def test_a_dead_process_is_reported_as_failed(app, clean):
    from ear_detection import backtest
    from ear_detection.db import BacktestRun, db

    with app.app_context():
        db.session.add(BacktestRun(status="running", trigger="scheduled", pid=999999))
        db.session.commit()
        assert backtest.running_run() is None
        run = BacktestRun.query.one()
        assert run.status == "failed" and "ended unexpectedly" in run.message


# ---------------- API ----------------

def test_api_lists_runs_and_only_admins_start_them(app, client, admin, clinician, clean, monkeypatch):
    from ear_detection import backtest

    monkeypatch.setattr(backtest, "launch", lambda run: os.getpid())

    listing = client.get("/api/v1/backtests", headers=clinician).get_json()
    assert listing["latest"] is None and listing["runs"] == []
    assert listing["schedule"]["window_days"] == 30

    assert client.post("/api/v1/backtests", headers=clinician).status_code == 403
    started = client.post("/api/v1/backtests", headers=admin)
    assert started.status_code == 202
    run_id = started.get_json()["run"]["id"]
    assert client.post("/api/v1/backtests", headers=admin).status_code == 409

    with app.app_context():
        from ear_detection.db import BacktestRun, db
        run = db.session.get(BacktestRun, run_id)
        backtest.execute(run, fixed("normal", False), set())

    listing = client.get("/api/v1/backtests", headers=clinician).get_json()
    assert listing["latest"]["id"] == run_id
    assert listing["latest"]["results"]["all"]["cases"] == 0
    assert listing["runs"][0]["summary"]["all"]["cases"] == 0
    assert listing["running"] is None
    assert client.get("/api/v1/backtests/%d" % run_id, headers=clinician).status_code == 200
    assert client.get("/api/v1/backtests/999999", headers=clinician).status_code == 404

    audit = client.get("/api/v1/admin/audit", headers=admin).get_json()["items"]
    assert {"backtest_started", "backtest_completed"} <= {e["action"] for e in audit}


def test_clearing_all_records_clears_backtests(app, client, admin, clean, monkeypatch):
    from ear_detection import backtest

    monkeypatch.setattr(backtest, "launch", lambda run: os.getpid())
    client.post("/api/v1/backtests", headers=admin)
    response = client.post("/api/v1/admin/records/clear", headers=admin,
                           json={"confirm": "DELETE ALL RECORDS", "password": ADMIN[2]})
    assert response.get_json()["cleared"]["backtests"] == 1
    assert client.get("/api/v1/backtests", headers=admin).get_json()["runs"] == []
