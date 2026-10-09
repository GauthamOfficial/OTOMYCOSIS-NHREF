"""Re-run the model in service on every surgeon reviewed case.

    python scripts/backtest.py               run a backtest now
    python scripts/backtest.py --run-id N    complete a run the service created

The service starts this once a day (see ear_detection/backtest.py), and an
administrator can start it from the Dashboard. Running it by hand does the
same and records the result in the same place.
"""

import argparse
import logging
import os
import time
import traceback

# TensorFlow start up chatter and deprecation notices mean nothing to whoever
# reads the log.
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
logging.getLogger("tensorflow").setLevel(logging.ERROR)

from common import BASE_DIR  # noqa: E402,F401  (puts src on the import path)
from ear_detection import backtest, retraining  # noqa: E402
from ear_detection.db import BacktestRun, ModelVersion, db, make_db_app, utcnow  # noqa: E402


def log(message):
    print("%s  %s" % (time.strftime("%H:%M:%S"), message), flush=True)


def predictor_factory(version):
    """Load a model version and return a function scoring one image."""
    from ear_detection.registry import ActiveModel

    log("loading model %s" % version.version)
    return backtest.predictor(ActiveModel(version))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", type=int, help="a run created by the service")
    args = parser.parse_args()

    app = make_db_app()
    with app.app_context():
        db.create_all()
        if args.run_id:
            run = db.session.get(BacktestRun, args.run_id)
            if run is None:
                log("backtest run %d not found" % args.run_id)
                return 1
        else:
            if backtest.running_run() is not None:
                log("a backtest is already running")
                return 1
            active = ModelVersion.query.filter_by(status="active").first()
            run = BacktestRun(status="running", trigger="manual",
                              model_version_id=active.id if active else None)
            db.session.add(run)
            db.session.commit()

        try:
            log("backtest %d started" % run.id)
            protected = retraining.dataset_hashes()
            log("%d original dataset images to exclude" % len(protected))
            backtest.execute(run, predictor_factory, protected)
            log(run.message)
            for alert in run.alerts or []:
                log("ALERT  " + alert)
        except Exception as exc:
            traceback.print_exc()
            db.session.rollback()
            run = db.session.get(BacktestRun, run.id)
            run.status = "failed"
            run.finished_at = utcnow()
            run.message = "The backtest failed: %s" % exc
            db.session.commit()
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
