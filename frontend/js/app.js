/* Ear Disease Detection System, front-end controller.
   Nawaloka Hospital PLC Research and Education Foundation. */

(function () {
  "use strict";

  // Served by Flask in normal use, so the API shares this origin. Opening the
  // file directly from disk falls back to a local development server.
  var API_BASE = window.location.protocol === "file:" ? "http://localhost:5000" : "";
  var ENDPOINT = API_BASE + "/api/v0.1/inference";

  var MAX_BYTES = 10 * 1024 * 1024;
  var ACCEPTED = ["image/jpeg", "image/png", "image/bmp"];

  var el = function (id) { return document.getElementById(id); };

  var dropzone    = el("dropzone");
  var fileInput   = el("file-input");
  var preview     = el("preview");
  var previewImg  = el("preview-img");
  var previewName = el("preview-name");
  var previewSize = el("preview-size");
  var clearBtn    = el("clear-btn");
  var cropToggle  = el("crop-toggle");
  var actionBtn   = el("predict-btn");
  var actionText  = actionBtn.querySelector(".action-text");
  var placeholder = el("placeholder");
  var busy        = el("busy");
  var resultBox   = el("result");
  var resultValue = el("result-value");
  var meterFill   = el("meter-fill");
  var confValue   = el("confidence-value");
  var rankingBody = el("ranking-body");
  var errorBox    = el("error");
  var errorText   = el("error-text");

  var selectedFile = null;
  var previewUrl = null;

  function formatSize(bytes) {
    if (bytes < 1024) { return bytes + " B"; }
    if (bytes < 1048576) { return (bytes / 1024).toFixed(0) + " KB"; }
    return (bytes / 1048576).toFixed(1) + " MB";
  }

  function percent(value) {
    return (Math.max(0, Math.min(1, value)) * 100).toFixed(1) + "%";
  }

  /* Only one of the four output states is visible at any time. */
  function showPane(which) {
    placeholder.hidden = which !== "idle";
    busy.hidden        = which !== "busy";
    resultBox.hidden   = which !== "result";
    errorBox.hidden    = which !== "error";
  }

  function fail(message) {
    errorText.textContent = message;
    showPane("error");
  }

  function releasePreview() {
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = null;
    }
  }

  function clearSelection() {
    releasePreview();
    selectedFile = null;
    fileInput.value = "";
    preview.hidden = true;
    previewImg.removeAttribute("src");
    actionBtn.disabled = true;
    showPane("idle");
  }

  function selectFile(file) {
    if (!file) { return; }

    if (ACCEPTED.indexOf(file.type) === -1) {
      fail("Unsupported file type. Use a JPG, PNG or BMP image.");
      return;
    }
    if (file.size > MAX_BYTES) {
      fail("Image exceeds the 10 MB limit. Use a smaller file.");
      return;
    }

    releasePreview();
    selectedFile = file;
    previewUrl = URL.createObjectURL(file);
    previewImg.src = previewUrl;
    previewName.textContent = file.name;
    previewSize.textContent = formatSize(file.size);
    preview.hidden = false;
    actionBtn.disabled = false;
    showPane("idle");
  }

  function renderDifferential(rows) {
    rankingBody.textContent = "";

    rows.forEach(function (row, index) {
      var tr = document.createElement("tr");
      if (index === 0) { tr.className = "is-top"; }

      var name = document.createElement("td");
      name.className = "rank-name";
      name.textContent = row.label;

      var value = document.createElement("td");
      value.className = "rank-value";

      var track = document.createElement("span");
      track.className = "rank-track";
      var bar = document.createElement("span");
      bar.style.width = percent(row.probability);
      track.appendChild(bar);

      value.appendChild(track);
      value.appendChild(document.createTextNode(percent(row.probability)));

      tr.appendChild(name);
      tr.appendChild(value);
      rankingBody.appendChild(tr);
    });
  }

  /* ---------- Selecting a file ---------- */

  dropzone.addEventListener("click", function () { fileInput.click(); });

  dropzone.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });

  fileInput.addEventListener("change", function () { selectFile(fileInput.files[0]); });

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

  /* ---------- Running the analysis ---------- */

  actionBtn.addEventListener("click", function () {
    if (!selectedFile) { return; }

    showPane("busy");
    actionBtn.disabled = true;
    actionText.textContent = "Analysing";

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

        var confidence = Number(data.confidence);
        if (isFinite(confidence)) {
          meterFill.style.width = percent(confidence);
          confValue.textContent = percent(confidence);
        } else {
          meterFill.style.width = "0";
          confValue.textContent = "n/a";
        }

        renderDifferential(
          Array.isArray(data.differential) && data.differential.length
            ? data.differential
            : [{ label: data.prediction, probability: confidence }]
        );

        showPane("result");
      })
      .catch(function (err) {
        fail(err instanceof TypeError
          ? "Could not reach the API. Confirm the service is running."
          : err.message);
      })
      .finally(function () {
        actionText.textContent = "Run Analysis";
        actionBtn.disabled = !selectedFile;
      });
  });

  window.addEventListener("pagehide", releasePreview);
})();
