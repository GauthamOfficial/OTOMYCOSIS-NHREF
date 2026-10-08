/* Ear Disease Detection System — front-end
   Nawaloka Hospital Research and Education Foundation */

(function () {
  "use strict";

  // When the page is served by Flask the API shares its origin. Opening the
  // file directly falls back to a local development server.
  var API_BASE = window.location.protocol === "file:"
    ? "http://localhost:5000"
    : "";
  var ENDPOINT = API_BASE + "/api/v0.1/inference";

  var MAX_BYTES = 10 * 1024 * 1024;
  var ACCEPTED = ["image/jpeg", "image/png", "image/bmp"];

  var dropzone    = document.getElementById("dropzone");
  var fileInput   = document.getElementById("file-input");
  var preview     = document.getElementById("preview");
  var previewImg  = document.getElementById("preview-img");
  var previewName = document.getElementById("preview-name");
  var previewSize = document.getElementById("preview-size");
  var clearBtn    = document.getElementById("clear-btn");
  var cropToggle  = document.getElementById("crop-toggle");
  var predictBtn  = document.getElementById("predict-btn");
  var resultBox   = document.getElementById("result");
  var resultValue = document.getElementById("result-value");
  var meterFill   = document.getElementById("meter-fill");
  var confValue   = document.getElementById("confidence-value");
  var errorBox    = document.getElementById("error");
  var errorText   = document.getElementById("error-text");

  var selectedFile = null;
  var previewUrl = null;

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function showError(message) {
    errorText.textContent = message;
    errorBox.hidden = false;
  }

  function hideError() {
    errorBox.hidden = true;
  }

  function revokePreview() {
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = null;
    }
  }

  function clearSelection() {
    revokePreview();
    selectedFile = null;
    fileInput.value = "";
    preview.hidden = true;
    previewImg.removeAttribute("src");
    predictBtn.disabled = true;
    resultBox.hidden = true;
    hideError();
  }

  function selectFile(file) {
    if (!file) return;

    if (ACCEPTED.indexOf(file.type) === -1) {
      showError("Unsupported file type. Please choose a JPG, PNG or BMP image.");
      return;
    }
    if (file.size > MAX_BYTES) {
      showError("That image is larger than 10 MB. Please choose a smaller file.");
      return;
    }

    hideError();
    resultBox.hidden = true;
    revokePreview();

    selectedFile = file;
    previewUrl = URL.createObjectURL(file);
    previewImg.src = previewUrl;
    previewName.textContent = file.name;
    previewSize.textContent = formatSize(file.size);
    preview.hidden = false;
    predictBtn.disabled = false;
  }

  /* ---------- Choosing a file ---------- */

  dropzone.addEventListener("click", function () {
    fileInput.click();
  });

  dropzone.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });

  fileInput.addEventListener("change", function () {
    selectFile(fileInput.files[0]);
  });

  ["dragenter", "dragover"].forEach(function (name) {
    dropzone.addEventListener(name, function (event) {
      event.preventDefault();
      dropzone.classList.add("is-dragover");
    });
  });

  ["dragleave", "drop"].forEach(function (name) {
    dropzone.addEventListener(name, function (event) {
      event.preventDefault();
      dropzone.classList.remove("is-dragover");
    });
  });

  dropzone.addEventListener("drop", function (event) {
    if (event.dataTransfer && event.dataTransfer.files.length) {
      selectFile(event.dataTransfer.files[0]);
    }
  });

  clearBtn.addEventListener("click", clearSelection);

  /* ---------- Running the prediction ---------- */

  predictBtn.addEventListener("click", function () {
    if (!selectedFile) return;

    hideError();
    resultBox.hidden = true;
    predictBtn.disabled = true;
    predictBtn.classList.add("is-loading");

    var form = new FormData();
    form.append("file", selectedFile);

    var url = ENDPOINT + (cropToggle.checked ? "?crop=true" : "");

    fetch(url, { method: "POST", body: form })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) {
            throw new Error(data.error || "The server rejected the request.");
          }
          return data;
        });
      })
      .then(function (data) {
        resultValue.textContent = data.prediction;

        var confidence = typeof data.confidence === "number"
          ? data.confidence
          : parseFloat(data.confidence);

        if (isFinite(confidence)) {
          var percent = Math.max(0, Math.min(100, confidence * 100));
          meterFill.style.width = percent + "%";
          confValue.textContent = percent.toFixed(1) + "%";
        } else {
          meterFill.style.width = "0";
          confValue.textContent = "Not reported";
        }

        resultBox.hidden = false;
      })
      .catch(function (err) {
        showError(
          err instanceof TypeError
            ? "Could not reach the API. Make sure the service is running."
            : err.message
        );
      })
      .finally(function () {
        predictBtn.classList.remove("is-loading");
        predictBtn.disabled = !selectedFile;
      });
  });

  window.addEventListener("pagehide", revokePreview);
})();
