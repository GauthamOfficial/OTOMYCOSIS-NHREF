"""Flask application serving the ear disease detection API and front-end."""

import os

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

from .config import (
    ALLOWED_EXTENSIONS,
    API_PREFIX,
    FRONTEND_DIR,
    LABELS_VERIFIED,
    UPLOAD_DIR,
)
from .model import assess, clear_session, group_totals, load_classifier, predict
from .preprocessing import load_image, load_image_cropped


def _is_allowed(filename):
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS


def create_app():
    app = Flask(__name__, static_folder=None)
    CORS(app)

    @app.route("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.route("/<path:filename>")
    def static_files(filename):
        return send_from_directory(FRONTEND_DIR, filename)

    @app.route("%s/health" % API_PREFIX)
    def health():
        return jsonify({"status": "ok"})

    @app.route("%s/inference" % API_PREFIX, methods=["POST"])
    def inference():
        if "file" not in request.files:
            return jsonify({"error": "No file was uploaded."}), 400

        uploaded = request.files["file"]
        if uploaded.filename == "":
            return jsonify({"error": "No file was selected."}), 400
        if not _is_allowed(uploaded.filename):
            return jsonify({"error": "Unsupported file type."}), 400

        if not os.path.isdir(UPLOAD_DIR):
            os.makedirs(UPLOAD_DIR)

        image_path = os.path.join(UPLOAD_DIR, secure_filename(uploaded.filename))
        uploaded.save(image_path)

        # ``crop=true`` trims the dark otoscope border before classifying.
        use_crop = request.args.get("crop", "").lower() in ("1", "true", "yes")
        batch = load_image_cropped(image_path) if use_crop else load_image(image_path)

        try:
            model = load_classifier()
            raw = model.predict(batch, verbose=0)[0]
            label, confidence, ranked = predict(model, batch)
            groups = group_totals(raw)
        finally:
            # The graph is released so the next request can load the model again.
            clear_session()

        certainty = assess(ranked)

        return jsonify({
            "prediction": label,
            "confidence": round(confidence, 4),
            # True when the distribution is too flat to call. Clients must not
            # present the leading class as a finding when this is set.
            "inconclusive": certainty["inconclusive"],
            "certainty": certainty,
            "differential": [
                {"label": name, "probability": round(value, 4)}
                for name, value in ranked
            ],
            # Coarse rollup, so a confident family is not hidden behind an
            # uncertain split between its own subtypes.
            "groups": groups,
            # False means the running weights are the legacy ones whose class
            # order was never recorded.
            "labels_verified": LABELS_VERIFIED,
        })

    return app
