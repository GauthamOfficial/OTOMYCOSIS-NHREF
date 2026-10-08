/* Screening, history, examination detail, dashboard and account views. */

(function (App) {
  "use strict";

  var r = App.r, el = App.el, pct = App.pct;
  var MAX_BYTES = 15 * 1024 * 1024;
  var ACCEPTED = ["image/jpeg", "image/png", "image/bmp", "image/tiff", "image/webp"];

  function setPrintPatient(exam) {
    document.getElementById("print-patient").textContent = exam
      ? "Record " + exam.record_no + ", " + App.capitalise(exam.sex) + ", " + exam.age_years +
        " years, " + exam.ear + " ear. Examined " + App.formatDate(exam.created_at) +
        " by " + exam.clinician + ". Model " + exam.model_version + "."
      : "";
    document.getElementById("print-stamp").textContent = "Printed " + new Date().toLocaleString();
  }

  /* ======================= Screening ======================= */

  App.views.screen = function (view) {
    App.mount(view, "tpl-screening");
    var form = r(view, "patient-form");
    var fileInput = r(view, "file");
    var dropzone = r(view, "dropzone");
    var analyseBtn = r(view, "analyse");
    var selected = null;
    var previewUrl = null;
    var knownPatient = null;

    function value(name) {
      var input = form.elements[name];
      if (input instanceof RadioNodeList) { return input.value; }
      return input ? input.value.trim() : "";
    }

    function ready() {
      var age = value("age");
      return !!(selected && value("record_no") && age !== "" && +age >= 0 && +age <= 120 &&
                value("sex") && value("ear"));
    }

    function refreshButton() { analyseBtn.disabled = !ready(); }

    /* Look the record number up so returning patients keep their recorded sex. */
    var recordInput = form.elements.record_no;
    var hint = r(view, "record-hint");
    var defaultHint = hint.textContent;
    recordInput.addEventListener("blur", function () {
      var record = recordInput.value.trim();
      knownPatient = null;
      hint.classList.remove("is-known");
      hint.textContent = defaultHint;
      if (!record) { return; }
      App.request("GET", "/patients/" + encodeURIComponent(record)).then(function (p) {
        recordInput.value = p.record_no;
        if (p.exists) {
          knownPatient = p;
          hint.classList.add("is-known");
          hint.textContent = "Existing patient, " + p.sex + ". " + p.examinations +
            " previous examination" + (p.examinations === 1 ? "" : "s") +
            ", last on " + App.formatDate(p.last_examined, false) + ".";
          var radio = form.querySelector("input[name=sex][value=" + p.sex + "]");
          if (radio) { radio.checked = true; }
        } else {
          hint.textContent = "New patient. A record will be created.";
        }
        refreshButton();
      }).catch(function () {});
    });

    form.addEventListener("input", refreshButton);
    form.addEventListener("change", refreshButton);

    function selectFile(file) {
      if (!file) { return; }
      if (file.type && ACCEPTED.indexOf(file.type) === -1) {
        App.showError(r(view, "form-error"), "Unsupported file type. Use a JPG, PNG, BMP, TIFF or WebP image.");
        return;
      }
      if (file.size > MAX_BYTES) {
        App.showError(r(view, "form-error"), "Image exceeds the 15 MB limit.");
        return;
      }
      App.showError(r(view, "form-error"), "");
      if (previewUrl) { URL.revokeObjectURL(previewUrl); }
      selected = file;
      previewUrl = URL.createObjectURL(file);
      r(view, "preview-img").src = previewUrl;
      r(view, "preview-name").textContent = file.name;
      r(view, "preview-size").textContent = App.formatSize(file.size);
      r(view, "preview").hidden = false;
      refreshButton();
    }

    dropzone.addEventListener("click", function () { fileInput.click(); });
    dropzone.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
    });
    fileInput.addEventListener("change", function () { selectFile(fileInput.files[0]); });
    ["dragenter", "dragover"].forEach(function (n) {
      dropzone.addEventListener(n, function (e) { e.preventDefault(); dropzone.classList.add("is-dragover"); });
    });
    ["dragleave", "drop"].forEach(function (n) {
      dropzone.addEventListener(n, function (e) { e.preventDefault(); dropzone.classList.remove("is-dragover"); });
    });
    dropzone.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files.length) { selectFile(e.dataTransfer.files[0]); }
    });
    r(view, "clear").addEventListener("click", function () {
      selected = null;
      fileInput.value = "";
      r(view, "preview").hidden = true;
      refreshButton();
    });

    function show(which) {
      r(view, "placeholder").hidden = which !== "idle";
      r(view, "busy").hidden = which !== "busy";
      r(view, "assessment").hidden = which !== "result";
      r(view, "result-actions").hidden = which !== "result";
    }

    analyseBtn.addEventListener("click", function () {
      if (!ready()) { return; }
      var data = new FormData();
      data.append("record_no", value("record_no"));
      data.append("age", value("age"));
      data.append("sex", value("sex"));
      data.append("ear", value("ear"));
      data.append("notes", value("notes"));
      data.append("file", selected);

      App.showError(r(view, "form-error"), "");
      analyseBtn.disabled = true;
      analyseBtn.querySelector(".action-text").textContent = "Analysing";
      show("busy");

      App.request("POST", "/examinations", { form: data })
        .then(function (exam) {
          App.result.renderAssessment(r(view, "assessment"), exam.result, { duplicate: exam.duplicate_of });
          r(view, "result-meta").textContent = "Examination " + exam.id + ", model " + exam.model_version;
          show("result");
          App.result.renderAttention(r(view, "attention"), exam.images, exam.result.finding.name);
          r(view, "attention-card").hidden = false;
          App.result.renderReview(r(view, "review"), exam);
          r(view, "review-card").hidden = false;
          r(view, "next").hidden = false;
          setPrintPatient(exam);
          /* Lock the form: this examination is now on record. */
          Array.prototype.forEach.call(form.elements, function (f) { f.disabled = true; });
          dropzone.style.pointerEvents = "none";
          r(view, "clear").hidden = true;
          analyseBtn.hidden = true;
        })
        .catch(function (err) {
          show("idle");
          App.showError(r(view, "form-error"), err.message);
          analyseBtn.disabled = false;
          analyseBtn.querySelector(".action-text").textContent = "Run Analysis";
        });
    });

    r(view, "new").addEventListener("click", function () {
      App.releaseUrls();
      App.views.screen(view);
      window.scrollTo(0, 0);
    });

    show("idle");
    recordInput.focus();
  };
  App.views.screen.title = ["Tympanic Membrane Screening",
    "Record the patient, analyse the otoscope image, then confirm or correct the finding."];

  /* ======================= History ======================= */

  App.views.history = function (view) {
    App.mount(view, "tpl-history");
    var filters = r(view, "filters");
    var page = 1;

    function load() {
      var params = new URLSearchParams({
        page: page, per_page: 25,
        q: filters.elements.q.value.trim(),
        status: filters.elements.status.value
      });
      App.request("GET", "/examinations?" + params.toString()).then(function (data) {
        var body = r(view, "rows");
        body.textContent = "";
        data.items.forEach(function (item) {
          var tr = el("tr", "is-link");
          tr.tabIndex = 0;
          tr.appendChild(el("td", null, App.formatDate(item.created_at)));
          tr.appendChild(el("td", null, item.record_no));
          tr.appendChild(el("td", null, App.capitalise(item.sex) + ", " + item.age_years));
          tr.appendChild(el("td", null, App.capitalise(item.ear)));
          var finding = el("td", null, item.predicted_name);
          finding.appendChild(el("span", "sub", pct(item.predicted_probability) +
            (item.low_confidence ? ", low confidence" : "")));
          tr.appendChild(finding);
          var rec = el("td");
          rec.appendChild(App.referralPill(item.referral));
          tr.appendChild(rec);
          var status = el("td");
          status.appendChild(App.statusPill(item.status));
          if (item.review && item.review.verdict === "corrected") {
            status.appendChild(el("span", "sub", "to " + (item.review.true_class_name || item.review.true_class)));
          } else if (item.review && item.review.verdict === "other") {
            status.appendChild(el("span", "sub", item.review.other_label));
          }
          tr.appendChild(status);
          tr.appendChild(el("td", "muted", item.clinician));
          function open() { location.hash = "#/exam/" + item.id; }
          tr.addEventListener("click", open);
          tr.addEventListener("keydown", function (e) { if (e.key === "Enter") { open(); } });
          body.appendChild(tr);
        });
        r(view, "empty").hidden = data.items.length > 0;
        r(view, "summary").textContent = data.total + " examination" + (data.total === 1 ? "" : "s") +
          ", page " + data.page + " of " + data.pages;
        r(view, "prev").disabled = data.page <= 1;
        r(view, "next").disabled = data.page >= data.pages;
      }).catch(function (err) { App.toast(err.message, true); });
    }

    filters.addEventListener("submit", function (e) { e.preventDefault(); page = 1; load(); });
    filters.elements.status.addEventListener("change", function () { page = 1; load(); });
    r(view, "prev").addEventListener("click", function () { page -= 1; load(); });
    r(view, "next").addEventListener("click", function () { page += 1; load(); });
    load();
  };
  App.views.history.title = ["Examination History",
    "Every examination on record. Open one to see the result or to review it."];

  /* ======================= Examination detail ======================= */

  App.views.exam = function (view, id) {
    App.mount(view, "tpl-exam");
    App.request("GET", "/examinations/" + id).then(function (exam) {
      document.getElementById("page-title").textContent = "Examination " + exam.id;
      document.getElementById("page-subtitle").textContent = "Record " + exam.record_no + ", " +
        App.formatDate(exam.created_at);

      var facts = r(view, "facts");
      [["Record number", exam.record_no],
       ["Patient", App.capitalise(exam.sex) + ", " + exam.age_years + " years"],
       ["Ear", App.capitalise(exam.ear)],
       ["Examined", App.formatDate(exam.created_at)],
       ["Clinician", exam.clinician],
       ["Model version", exam.model_version],
       ["Notes", exam.notes || "None"]].forEach(function (pair) {
        facts.appendChild(el("dt", null, pair[0]));
        facts.appendChild(el("dd", null, pair[1]));
      });

      App.imageUrl(exam.images.original).then(function (u) { r(view, "original").src = u; })
        .catch(function () { r(view, "original").alt = "Original image unavailable"; });
      App.result.renderAssessment(r(view, "assessment"), exam.result);
      r(view, "result-meta").textContent = "Model " + exam.model_version;
      App.result.renderAttention(r(view, "attention"), exam.images, exam.result.finding.name);
      App.result.renderReview(r(view, "review"), exam);
      setPrintPatient(exam);
    }).catch(function (err) {
      view.textContent = "";
      var main = el("main", "shell");
      main.style.padding = "40px 28px";
      main.appendChild(el("p", "empty", err.message));
      view.appendChild(main);
    });
  };
  App.views.exam.title = ["Examination", ""];

  /* ======================= Dashboard ======================= */

  function statBox(label, value, sub) {
    var box = el("div", "stat");
    box.appendChild(el("span", "stat-label", label));
    box.appendChild(el("span", "stat-value", value));
    box.appendChild(el("span", "stat-ci", sub || ""));
    return box;
  }

  function ciText(p) {
    if (!p.total) { return "no reviewed cases yet"; }
    var range = p.ci && p.ci[0] !== null ? "95% CI " + pct(p.ci[0], 0) + " to " + pct(p.ci[1], 0) + ", " : "";
    return range + p.successes + " of " + p.total;
  }

  App.views.dashboard = function (view) {
    App.mount(view, "tpl-dashboard");
    App.request("GET", "/stats").then(function (s) {
      var activity = r(view, "activity");
      activity.appendChild(statBox("Examinations", s.totals.examinations, "on record"));
      activity.appendChild(statBox("Reviewed", s.totals.reviewed, "by a surgeon"));
      activity.appendChild(statBox("Awaiting review", s.totals.unreviewed, ""));
      activity.appendChild(statBox("Referred", s.totals.referred, "by the model"));

      var labelled = s.agreement.total;
      var caution = r(view, "caution");
      if (labelled < 30) {
        caution.textContent = "Only " + labelled + " reviewed case" + (labelled === 1 ? "" : "s") +
          " so far. The figures below are too uncertain to rely on until at least 30, and ideally " +
          "several hundred, cases have been reviewed.";
        caution.hidden = false;
      }

      var perf = r(view, "performance");
      perf.appendChild(statBox("Surgeon agreed", labelled ? pct(s.agreement.value) : "n/a", ciText(s.agreement)));
      perf.appendChild(statBox("Referral sensitivity", s.referral.sensitivity.total ? pct(s.referral.sensitivity.value) : "n/a",
        ciText(s.referral.sensitivity)));
      perf.appendChild(statBox("Referral specificity", s.referral.specificity.total ? pct(s.referral.specificity.value) : "n/a",
        ciText(s.referral.specificity)));
      perf.appendChild(statBox("Missed abnormal ears", s.referral.missed, "not referred, surgeon found disease"));

      var body = r(view, "per-class");
      s.per_class.forEach(function (c) {
        var tr = el("tr");
        tr.appendChild(el("td", null, c.name));
        tr.appendChild(el("td", "num", c.cases));
        tr.appendChild(el("td", "num", c.recall.total ? pct(c.recall.value, 0) : "n/a"));
        tr.appendChild(el("td", "num", c.precision.total ? pct(c.precision.value, 0) : "n/a"));
        body.appendChild(tr);
      });

      function list(box, items, render, emptyText) {
        if (!items.length) { box.appendChild(el("li", "muted", emptyText)); return; }
        items.forEach(function (item) {
          var li = el("li");
          li.appendChild(el("span", null, render(item)));
          li.appendChild(el("span", "count", item.count));
          box.appendChild(li);
        });
      }
      list(r(view, "corrections"), s.corrections, function (c) {
        return "Predicted " + c.predicted + ", surgeon found " + c.actual;
      }, "No corrections yet.");
      list(r(view, "others"), s.other_diagnoses, function (o) { return App.capitalise(o.label); },
        "None entered yet.");
    }).catch(function (err) { App.toast(err.message, true); });
  };
  App.views.dashboard.title = ["Dashboard",
    "How the model performs on this clinic's patients, judged by the surgeons' reviews."];

  /* ======================= Account ======================= */

  App.passwordForm = function (form, onDone) {
    /* Replace the node so repeated calls never stack submit handlers. */
    var fresh = form.cloneNode(false);
    form.parentNode.replaceChild(fresh, form);
    form = fresh;
    form.appendChild(document.getElementById("tpl-password-fields").content.cloneNode(true));
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var error = r(form, "error");
      var current = form.elements.current.value;
      var next = form.elements["new"].value;
      if (next !== form.elements.repeat.value) {
        App.showError(error, "The new passwords do not match.");
        return;
      }
      var button = form.querySelector("button[type=submit]");
      button.disabled = true;
      App.request("POST", "/auth/password", { json: { current_password: current, new_password: next } })
        .then(function (data) {
          App.state.token = data.access_token;
          App.state.user = data.user;
          form.reset();
          App.showError(error, "");
          onDone(data.user);
        })
        .catch(function (err) { App.showError(error, err.message); })
        .finally(function () { button.disabled = false; });
    });
  };

  App.views.account = function (view) {
    App.mount(view, "tpl-account");
    App.passwordForm(r(view, "form"), function () { App.toast("Password changed."); });
  };
  App.views.account.title = ["Account", "Signed in as " + ""];
})(window.App);
