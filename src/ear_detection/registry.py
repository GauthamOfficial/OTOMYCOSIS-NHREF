"""Which trained model is in service, and swapping it without a restart.

Model versions live in the database. Exactly one is ``active``; retraining
adds ``candidate`` versions, and approving one makes it active. The new model
is fully loaded before the swap, so requests never see a half loaded model.
"""

import logging
import os
import threading

from . import config
from .db import ModelVersion, db

log = logging.getLogger("ear_detection")


def summarise(evaluation):
    """The handful of figures used to compare model versions."""
    referral = (evaluation or {}).get("referral", {})
    test = (evaluation or {}).get("test", {})
    if not referral:
        return {}
    return {
        "test_images": test.get("images"),
        "sensitivity": referral.get("sensitivity"),
        "sensitivity_ci": referral.get("sensitivity_ci"),
        "specificity": referral.get("specificity"),
        "specificity_ci": referral.get("specificity_ci"),
        "auroc": referral.get("auroc"),
        "threshold": referral.get("threshold"),
        "macro_f1": test.get("macro_f1"),
        "accuracy": test.get("accuracy"),
    }


def ensure_seed_version():
    """Register the shipped model as the first version on a new database."""
    if ModelVersion.query.first() is not None:
        return
    evaluation = config.read_json(config.EVALUATION_FILE, default={})
    db.session.add(ModelVersion(
        version=config.DEFAULT_MODEL_VERSION,
        status="active",
        model_path=config.relative(config.DEFAULT_MODEL_PATH),
        calibration_path=config.relative(config.CALIBRATION_FILE),
        evaluation_path=config.relative(config.EVALUATION_FILE),
        summary=summarise(evaluation),
        notes="Initial release, trained on the public datasets.",
    ))
    db.session.commit()


class ActiveModel:
    """An immutable snapshot of one loaded model version."""

    def __init__(self, version_row):
        # Imported here so database tools can use this module without TensorFlow.
        from .model import Classifier

        self.version_id = version_row.id
        self.version = version_row.version
        self.meta = config.model_metadata(
            version_row.model_path, version_row.calibration_path, version_row.evaluation_path)
        if not os.path.isfile(self.meta["model_path"]):
            raise RuntimeError("Model file for version %s not found at %s"
                               % (self.version, self.meta["model_path"]))
        self.classifier = Classifier(
            self.meta["model_path"], self.meta["classes"],
            temperature=self.meta["temperature"], use_tta=self.meta["use_tta"],
        )


class Registry:
    def __init__(self):
        self._lock = threading.Lock()
        self._active = None

    def load_active(self):
        row = ModelVersion.query.filter_by(status="active").first()
        if row is None:
            raise RuntimeError("No active model version in the database.")
        loaded = ActiveModel(row)
        with self._lock:
            self._active = loaded
        log.info("serving model version %s", loaded.version)
        return loaded

    def current(self):
        with self._lock:
            return self._active

    def activate(self, version_row):
        """Load a version, then swap it in. Raises before swapping on failure."""
        loaded = ActiveModel(version_row)
        with self._lock:
            self._active = loaded
        log.info("switched to model version %s", loaded.version)
        return loaded
