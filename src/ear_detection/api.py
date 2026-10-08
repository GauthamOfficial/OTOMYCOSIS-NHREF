"""Flask application serving the ear disease detection API and front-end."""

import os

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

from .config import ALLOWED_EXTENSIONS, API_PREFIX, FRONTEND_DIR, UPLOAD_DIR
from .model import clear_session, load_classifier, predict
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
            label, confidence = predict(model, batch)
        finally:
            # The graph is released so the next request can load the model again.
            clear_session()

        return jsonify({"prediction": label, "confidence": round(confidence, 4)})

    return app
