"""Flask application serving the screening API and the web interface.

Patient images are handled in memory only. The upload is read, normalised,
analysed and dropped; nothing derived from it is written to disk, and the
uploaded filename is never logged, since clinic filenames often carry
patient names or record numbers.
"""

import base64
import io
import logging
import os
import time

import numpy as np
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from PIL import Image, UnidentifiedImageError
from werkzeug.exceptions import RequestEntityTooLarge

from . import __version__, config, imaging
from .model import Classifier, assess, render_overlay

log = logging.getLogger("ear_detection")


def _data_uri(array):
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _quality_warnings(stats, ranges):
    """Flag inputs outside the range the model was trained on."""
    warnings = []
    brightness = ranges.get("brightness", {})
    sharpness = ranges.get("sharpness", {})
    if brightness and stats["brightness"] < brightness["p01"]:
        warnings.append("Image is darker than 99% of training images. Results may be unreliable.")
    if brightness and stats["brightness"] > brightness["p99"]:
        warnings.append("Image is brighter than 99% of training images. Results may be unreliable.")
    if sharpness and stats["sharpness"] < sharpness["p01"]:
        warnings.append("Image is blurrier than 99% of training images. Results may be unreliable.")
    return warnings


def create_app():
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES
    CORS(app)

    meta = config.load_runtime_metadata()
    classes = meta["classes"]
    normal_index = classes.index(config.NORMAL_CLASS)
    threshold = float(meta["referral_threshold"])

    started = time.time()
    classifier = Classifier(
        config.MODEL_PATH, classes,
        temperature=meta["temperature"], use_tta=meta["use_tta"],
    )
    log.info("model loaded in %.1fs, %d classes, calibrated=%s",
             time.time() - started, len(classes), meta["calibrated"])

    def display(name):
        return config.CLASS_INFO.get(name, (name, ""))[0]

    def describe(name):
        return config.CLASS_INFO.get(name, (name, ""))[1]

    # ---------------- static interface ----------------

    @app.route("/")
    def index():
        return send_from_directory(config.FRONTEND_DIR, "index.html")

    @app.route("/<path:filename>")
    def static_files(filename):
        return send_from_directory(config.FRONTEND_DIR, filename)

    # ---------------- API ----------------

    @app.route(config.API_PREFIX + "/health")
    def health():
        return jsonify({
            "status": "ok",
            "version": __version__,
            "classes": len(classes),
            "calibrated": meta["calibrated"],
        })

    @app.route(config.API_PREFIX + "/model")
    def model_card():
        evaluation = meta["evaluation"] or {}
        referral = evaluation.get("referral", {})
        test = evaluation.get("test", {})
        return jsonify({
            "version": __version__,
            "architecture": "MobileNetV2, ImageNet initialised, fine tuned",
            "calibrated": meta["calibrated"],
            "referral_threshold": threshold,
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
                {"class": c, "name": display(c), "description": describe(c)}
                for c in classes
            ],
        })

    @app.route(config.API_PREFIX + "/analyse", methods=["POST"])
    def analyse():
        upload = request.files.get("file")
        if upload is None or upload.filename == "":
            return jsonify({"error": "No image was uploaded."}), 400
        if os.path.splitext(upload.filename)[1].lower() not in config.ALLOWED_EXTENSIONS:
            return jsonify({"error": "Unsupported file type. Use JPG, PNG, BMP, TIFF or WebP."}), 400

        t0 = time.time()
        try:
            image = imaging.open_image(upload.read())
        except (UnidentifiedImageError, OSError, ValueError):
            return jsonify({"error": "The file could not be read as an image."}), 400

        if min(image.size) < config.MIN_IMAGE_SIDE:
            return jsonify({
                "error": "Image is too small (%dx%d). Use at least %d pixels on the short side."
                         % (image.size[0], image.size[1], config.MIN_IMAGE_SIDE)
            }), 400

        array = imaging.normalize(image)
        del image

        probabilities, feats = classifier.predict(array)
        order = np.argsort(probabilities)[::-1]
        top = int(order[0])
        margin, entropy_ratio = assess(probabilities)
        low_confidence = (
            margin < config.CONFIDENCE_MIN_MARGIN
            or entropy_ratio > config.CONFIDENCE_MAX_ENTROPY_RATIO
        )

        abnormal = float(1.0 - probabilities[normal_index])
        refer = abnormal >= threshold

        groups = []
        by_name = {c: float(probabilities[i]) for i, c in enumerate(classes)}
        for parent, members in meta["parent_groups"].items():
            groups.append({
                "name": config.PARENT_DISPLAY_NAMES.get(parent, parent),
                "probability": round(sum(by_name.get(m, 0.0) for m in members), 4),
            })

        overlay = render_overlay(array, classifier.activation_map(feats, top))
        stats = imaging.input_statistics(array)

        elapsed = time.time() - t0
        log.info("analysed in %.2fs: refer=%s finding=%s", elapsed, refer, classes[top])

        response = jsonify({
            "referral": {
                "recommendation": "refer" if refer else "routine",
                "abnormal_probability": round(abnormal, 4),
                "threshold": round(threshold, 4),
            },
            "finding": {
                "class": classes[top],
                "name": display(classes[top]),
                "description": describe(classes[top]),
                "probability": round(float(probabilities[top]), 4),
                "low_confidence": bool(low_confidence),
                "margin": round(margin, 4),
                "entropy_ratio": round(entropy_ratio, 4),
            },
            "differential": [
                {
                    "class": classes[i],
                    "name": display(classes[i]),
                    "probability": round(float(probabilities[i]), 4),
                }
                for i in order
            ],
            "groups": groups,
            "quality": {
                "warnings": _quality_warnings(stats, meta["input_stats"]),
                "brightness": round(stats["brightness"], 1),
                "sharpness": round(stats["sharpness"], 1),
            },
            "explanation": {
                "class": classes[top],
                "model_view": _data_uri(array),
                "overlay": _data_uri(overlay),
            },
            "meta": {
                "version": __version__,
                "calibrated": meta["calibrated"],
                "seconds": round(elapsed, 2),
            },
        })
        # Results describe a patient; keep them out of browser and proxy caches.
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify({
            "error": "Image exceeds the %d MB limit." % (config.MAX_UPLOAD_BYTES // (1024 * 1024))
        }), 413

    return app
