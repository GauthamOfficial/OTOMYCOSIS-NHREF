"""Start and monitor retraining runs.

Training takes tens of minutes on a CPU, so it runs as a separate process
(scripts/retrain.py) rather than inside the web server. The run writes its own
progress to the database; this module starts it, checks it is still alive,
and reports whether the prerequisites are in place.
"""

import os
import subprocess
import sys

from . import config
from .db import Examination, TrainingJob, db, utcnow

RETRAIN_SCRIPT = os.path.join(config.BASE_DIR, "scripts", "retrain.py")
JOB_LOG_DIR = os.path.join(config.STORAGE_DIR, "jobs")
DATASET_DIR = os.path.join(config.BASE_DIR, "Dataset")
MANIFEST = os.path.join(config.ARTIFACTS_DIR, "manifest.csv")


_HASHES = {}


def dataset_hashes(splits=("train", "val", "test")):
    """SHA 256 of every image in the given splits of the original dataset.

    Computed once per process and split selection, since the dataset never
    changes. Empty when the dataset is not on this computer.
    """
    key = tuple(sorted(splits))
    if key not in _HASHES:
        import csv
        import hashlib

        hashes = set()
        if os.path.isfile(MANIFEST):
            with open(MANIFEST, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if row["split"] not in key:
                        continue
                    path = os.path.join(config.BASE_DIR, row["path"])
                    if os.path.isfile(path):
                        with open(path, "rb") as img:
                            hashes.add(hashlib.sha256(img.read()).hexdigest())
        _HASHES[key] = hashes
    return _HASHES[key]


def frozen_hashes():
    """SHA 256 of every validation and test image in the original dataset.

    Uploads identical to one of these are never trained on: doing so would
    contaminate the frozen comparison every candidate is judged by.
    """
    return dataset_hashes(("val", "test"))


def trainable_examinations():
    """Reviewed examinations a retraining run may learn from.

    The latest review must name a trained class, and the image must not be a
    copy of a frozen validation or test image.
    """
    protected = frozen_hashes()
    usable, excluded = [], 0
    for exam in Examination.query.all():
        review = exam.latest_review
        if review is None or not review.label:
            continue
        if exam.image_sha256 in protected:
            excluded += 1
            continue
        usable.append((exam, review.label))
    return usable, excluded


def usable_reviews():
    return len(trainable_examinations()[0])


def process_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) terminates the process on Windows, so ask the kernel.
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def reconcile(job):
    """Mark a job failed if its process died without reporting back."""
    if job is not None and job.status == "running" and not process_alive(job.pid):
        db.session.refresh(job)
        if job.status == "running":
            job.status = "failed"
            job.finished_at = utcnow()
            job.message = (job.message or "") + " The training process ended unexpectedly."
            db.session.commit()
    return job


def running_job():
    job = TrainingJob.query.filter_by(status="running").order_by(TrainingJob.id.desc()).first()
    reconcile(job)
    return job if job is not None and job.status == "running" else None


def readiness():
    reviews = usable_reviews()
    dataset = os.path.isdir(DATASET_DIR) and os.path.isfile(MANIFEST)
    job = running_job()
    problems = []
    if reviews < config.MIN_REVIEWS_FOR_RETRAIN:
        problems.append("%d reviewed cases are needed; %d are available."
                        % (config.MIN_REVIEWS_FOR_RETRAIN, reviews))
    if not dataset:
        problems.append("The original Dataset folder and artifacts/manifest.csv are needed on "
                        "this computer, because new cases are added to the original training "
                        "data rather than replacing it.")
    if job is not None:
        problems.append("A retraining run is already in progress.")
    return {
        "usable_reviews": reviews,
        "minimum_reviews": config.MIN_REVIEWS_FOR_RETRAIN,
        "dataset_available": dataset,
        "running_job": job.to_dict() if job else None,
        "ready": not problems,
        "problems": problems,
    }


def start(user):
    """Create a job record and launch the training process."""
    job = TrainingJob(status="running", started_by_id=user.id, message="Starting.")
    db.session.add(job)
    db.session.commit()

    os.makedirs(JOB_LOG_DIR, exist_ok=True)
    log_path = os.path.join(JOB_LOG_DIR, "job_%d.log" % job.id)
    job.log_path = config.relative(log_path)

    env = dict(os.environ, PYTHONUNBUFFERED="1", TF_CPP_MIN_LOG_LEVEL="2")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with open(log_path, "w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, RETRAIN_SCRIPT, "--job-id", str(job.id)],
            cwd=config.BASE_DIR, stdout=log, stderr=subprocess.STDOUT,
            env=env, creationflags=flags,
        )
    job.pid = process.pid
    db.session.commit()
    return job


def log_tail(job, lines=40):
    if not job or not job.log_path:
        return ""
    path = config.absolute(job.log_path)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        content = fh.read().splitlines()
    # TensorFlow start up chatter means nothing to the person watching.
    noise = ("oneDNN", "TF-TRT", "cpu_feature_guard", "WARNING:tensorflow")
    kept = [line for line in content if not any(n in line for n in noise)]
    return "\n".join(kept[-lines:])
