/* Ear Disease Detection System, front-end controller.
   Nawaloka Hospital PLC Research and Education Foundation. */

(function () {
  "use strict";

  // Served by the Python service in normal use, so the API shares this
  // origin. Opening the file straight from disk falls back to a local server.
  var API = (window.location.protocol === "file:" ? "http://localhost:5000" : "") + "/api/v1";

  var MAX_BYTES = 15 * 1024 * 1024;
  var ACCEPTED = ["image/jpeg", "image/png", "image/bmp", "image/tiff", "image/webp"];
  var DIFFERENTIAL_ROWS = 5;
  var CHRONIC_PREFIX = "chronic_om_";

  var $ = function (id) { return document.getElementById(id); };

  var dropzone   = $("dropzone");
  var fileInput  = $("file-input");
  var preview    = $("preview");
  var actionBtn  = $("predict-btn");
  var actionText = actionBtn.querySelector(".action-text");

  var selectedFile = null;
  var previewUrl = null;
  var lastDifferential = [];
  var showAllRows = false;

  /* ---------------- helpers ---------------- */

  function pct(value, digits) {
    var v = Math.max(0, Math.min(1, Number(value) || 0));
    return (v * 100).toFixed(digits === undefined ? 1 : digits) + "%";
  }

  function formatSize(bytes) {
    if (bytes < 1024) { return bytes + " B"; }
    if (bytes < 1048576) { return (bytes / 1024).toFixed(0) + " KB"; }
    return (bytes / 1048576).toFixed(1) + " MB";
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  /* Only one of the four assessment states is visible at a time. */
  function showPane(which) {
    $("placeholder").hidden = which !== "idle";
    $("busy").hidden        = which !== "busy";
    $("result").hidden      = which !== "result";
    $("error").hidden       = which !== "error";
    if (which !== "result") { $("attention").hidden = true; }
  }

  function fail(message) {
    $("error-text").textContent = message;
    showPane("error");
  }

  function releasePreview() {
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = null;
    }
  }

  /* ---------------- selecting a file ---------------- */

  function clearSelection() {
    releasePreview();
    selectedFile = null;
    fileInput.value = "";
    preview.hidden = true;
    $("preview-img").removeAttribute("src");
    actionBtn.disabled = true;
    showPane("idle");
  }

  function selectFile(file) {
    if (!file) { return; }
    if (file.type && ACCEPTED.indexOf(file.type) === -1) {
      fail("Unsupported file type. Use a JPG, PNG, BMP, TIFF or WebP image.");
      return;
    }
    if (file.size > MAX_BYTES) {
      fail("Image exceeds the 15 MB limit.");
      return;
    }

    releasePreview();
    selectedFile = file;
    previewUrl = URL.createObjectURL(file);
    $("preview-img").src = previewUrl;
    $("preview-name").textContent = file.name;
    $("preview-size").textContent = formatSize(file.size);
    preview.hidden = false;
    actionBtn.disabled = false;
    showPane("idle");
  }

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

  $("clear-btn").addEventListener("click", clearSelection);

  /* ---------------- rendering a result ---------------- */

  function renderReferral(referral, finding) {
    var box = $("referral");
    var refer = referral.recommendation === "refer";
    var abnormal = referral.abnormal_probability;
    var threshold = referral.threshold;

    box.classList.toggle("is-refer", refer);
    box.classList.toggle("is-routine", !refer);
    $("referral-value").textContent = refer ? "Refer for ENT review" : "No abnormality detected";

    var text;
    if (refer && finding.class === "normal") {
      text = "The most likely finding is a normal ear, but the model is not confident " +
             "enough to rule out disease, so referral is recommended.";
    } else if (refer) {
      text = "The probability of an abnormal ear is " + pct(abnormal) +
             ", above the referral threshold of " + pct(threshold) + ".";
    } else {
      text = "The probability of an abnormal ear is " + pct(abnormal) +
             ", below the referral threshold of " + pct(threshold) +
             ". Follow up according to clinical judgement.";
    }
    $("referral-explain").textContent = text;

    $("gauge-fill").style.width = pct(abnormal);
    $("gauge-mark").style.left = pct(threshold);
    $("gauge-value").textContent = pct(abnormal);
    $("gauge-threshold").textContent = pct(threshold);
  }

  function renderQuality(quality) {
    var list = $("quality-list");
    list.textContent = "";
    var warnings = (quality && quality.warnings) || [];
    warnings.forEach(function (w) { list.appendChild(el("li", null, w)); });
    $("quality").hidden = warnings.length === 0;
  }

  function renderFinding(finding) {
    var box = document.querySelector(".finding");
    box.classList.toggle("is-low", !!finding.low_confidence);
    $("finding-value").textContent = finding.name;
    $("finding-desc").textContent = finding.description || "";
    $("finding-fill").style.width = pct(finding.probability);
    $("finding-prob").textContent = pct(finding.probability);
    $("finding-flag").hidden = !finding.low_confidence;

    var note = $("finding-note");
    if (finding.low_confidence) {
      note.textContent = "The leading conditions are close (" +
        (finding.margin * 100).toFixed(1) + " points apart). Treat this finding as " +
        "uncertain and read the differential below.";
      note.hidden = false;
    } else {
      note.hidden = true;
    }
  }

  function renderGroups(groups, finding) {
    var line = $("groups");
    line.textContent = "";
    var relevant = (groups || []).filter(function (g) {
      return g.probability >= 0.25 || finding.class.indexOf(CHRONIC_PREFIX) === 0;
    });
    if (!relevant.length) {
      line.hidden = true;
      return;
    }
    relevant.forEach(function (g, i) {
      if (i) { line.appendChild(document.createTextNode("  ")); }
      line.appendChild(document.createTextNode(g.name + ": "));
      line.appendChild(el("strong", null, pct(g.probability)));
    });
    line.hidden = false;
  }

  function renderDifferential() {
    var body = $("ranking-body");
    body.textContent = "";
    var rows = showAllRows ? lastDifferential : lastDifferential.slice(0, DIFFERENTIAL_ROWS);

    rows.forEach(function (row, index) {
      var tr = el("tr", index === 0 ? "is-top" : "");
      tr.appendChild(el("td", "rank-name", row.name));

      var value = el("td", "rank-value");
      var track = el("span", "rank-track");
      var bar = el("span");
      bar.style.width = pct(row.probability);
      track.appendChild(bar);
      value.appendChild(track);
      value.appendChild(document.createTextNode(pct(row.probability)));
      tr.appendChild(value);
      body.appendChild(tr);
    });

    var toggle = $("ranking-toggle");
    toggle.hidden = lastDifferential.length <= DIFFERENTIAL_ROWS;
    toggle.textContent = showAllRows
      ? "Show top " + DIFFERENTIAL_ROWS + " only"
      : "Show all " + lastDifferential.length + " conditions";
  }

  $("ranking-toggle").addEventListener("click", function () {
    showAllRows = !showAllRows;
    renderDifferential();
  });

  function renderAttention(explanation, finding) {
    $("model-view").src = explanation.model_view;
    $("model-overlay").src = explanation.overlay;
    $("overlay-class").textContent = finding.name;
    $("attention").hidden = false;
  }

  function render(data) {
    renderReferral(data.referral, data.finding);
    renderQuality(data.quality);
    renderFinding(data.finding);
    renderGroups(data.groups, data.finding);

    lastDifferential = data.differential || [];
    showAllRows = false;
    renderDifferential();

    $("result-meta").textContent = "Analysed in " + data.meta.seconds.toFixed(2) +
      " s, model " + data.meta.version + (data.meta.calibrated ? "" : ", uncalibrated");
    $("print-stamp").textContent = "Printed " + new Date().toLocaleString();

    showPane("result");
    renderAttention(data.explanation, data.finding);
  }

  /* ---------------- running the analysis ---------------- */

  actionBtn.addEventListener("click", function () {
    if (!selectedFile) { return; }

    showPane("busy");
    actionBtn.disabled = true;
    actionText.textContent = "Analysing";

    var form = new FormData();
    form.append("file", selectedFile);

    fetch(API + "/analyse", { method: "POST", body: form })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          if (!response.ok) {
            throw new Error(data.error || "The server rejected the request (" + response.status + ").");
          }
          return data;
        });
      })
      .then(render)
      .catch(function (err) {
        fail(err instanceof TypeError
          ? "Could not reach the analysis service. Confirm it is running."
          : err.message);
      })
      .finally(function () {
        actionText.textContent = "Run Analysis";
        actionBtn.disabled = !selectedFile;
      });
  });

  $("print-btn").addEventListener("click", function () { window.print(); });

  /* ---------------- model information ---------------- */

  function loadModelInfo() {
    fetch(API + "/model")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (info) {
        if (!info) { return; }

        $("build-tag").textContent = "Model " + info.version;

        var body = $("reference-body");
        body.textContent = "";
        (info.classes || []).forEach(function (c) {
          var tr = el("tr");
          var th = el("th", null, c.name);
          th.setAttribute("scope", "row");
          tr.appendChild(th);
          tr.appendChild(el("td", null, c.description));
          body.appendChild(tr);
        });

        var t = info.test_set || {};
        if (t.sensitivity === undefined || t.sensitivity === null) { return; }
        $("v-sens").textContent = pct(t.sensitivity);
        $("v-spec").textContent = pct(t.specificity);
        $("v-auc").textContent = Number(t.auroc).toFixed(3);
        $("v-n").textContent = t.images;
        if (t.sensitivity_ci) {
          $("v-sens-ci").textContent = "95% CI " + pct(t.sensitivity_ci[0]) + " to " + pct(t.sensitivity_ci[1]);
        }
        if (t.specificity_ci) {
          $("v-spec-ci").textContent = "95% CI " + pct(t.specificity_ci[0]) + " to " + pct(t.specificity_ci[1]);
        }
        $("validation").hidden = false;
      })
      .catch(function () { /* the interface still works without it */ });
  }

  loadModelInfo();
  window.addEventListener("pagehide", releasePreview);
})();
