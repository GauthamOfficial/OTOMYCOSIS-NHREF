/* Rendering of one examination's result and the surgeon review panel.
   Used by both the screening page and the examination detail page, so a
   stored result always looks exactly like a live one. */

(function (App) {
  "use strict";

  var r = App.r, el = App.el, pct = App.pct;
  var DIFFERENTIAL_ROWS = 5;
  var CHRONIC_PREFIX = "chronic_om_";

  /* ---------------- assessment ---------------- */

  function renderAssessment(container, result, options) {
    options = options || {};
    App.mount(container, "tpl-assessment");
    var referral = result.referral, finding = result.finding;
    var refer = referral.recommendation === "refer";

    var dup = r(container, "duplicate");
    if (options.duplicate) {
      dup.textContent = "This exact image was analysed before, on " +
        App.formatDate(options.duplicate.created_at) + " (examination " + options.duplicate.id + ").";
      dup.hidden = false;
    }

    var box = r(container, "referral");
    box.classList.add(refer ? "is-refer" : "is-routine");
    r(container, "referral-value").textContent = refer ? "Refer for ENT review" : "No abnormality detected";
    var explain;
    if (refer && finding["class"] === "normal") {
      explain = "The most likely finding is a normal ear, but the model is not confident enough " +
                "to rule out disease, so referral is recommended.";
    } else if (refer) {
      explain = "The probability of an abnormal ear is " + pct(referral.abnormal_probability) +
                ", above the referral threshold of " + pct(referral.threshold) + ".";
    } else {
      explain = "The probability of an abnormal ear is " + pct(referral.abnormal_probability) +
                ", below the referral threshold of " + pct(referral.threshold) +
                ". Follow up according to clinical judgement.";
    }
    r(container, "referral-explain").textContent = explain;
    r(container, "gauge-fill").style.width = pct(referral.abnormal_probability);
    r(container, "gauge-mark").style.left = pct(referral.threshold);
    r(container, "gauge-value").textContent = pct(referral.abnormal_probability);
    r(container, "gauge-threshold").textContent = pct(referral.threshold);

    var warnings = (result.quality && result.quality.warnings) || [];
    var list = r(container, "quality-list");
    warnings.forEach(function (w) { list.appendChild(el("li", null, w)); });
    r(container, "quality").hidden = warnings.length === 0;

    var findingBox = r(container, "finding");
    findingBox.classList.toggle("is-low", !!finding.low_confidence);
    r(container, "finding-value").textContent = finding.name;
    r(container, "finding-desc").textContent = finding.description || "";
    r(container, "finding-fill").style.width = pct(finding.probability);
    r(container, "finding-prob").textContent = pct(finding.probability);
    r(container, "finding-flag").hidden = !finding.low_confidence;
    if (finding.low_confidence) {
      var note = r(container, "finding-note");
      note.textContent = "The leading conditions are close (" + (finding.margin * 100).toFixed(1) +
        " points apart). Treat this finding as uncertain and read the differential below.";
      note.hidden = false;
    }

    var groupLine = r(container, "groups");
    (result.groups || []).forEach(function (g) {
      if (g.probability >= 0.25 || finding["class"].indexOf(CHRONIC_PREFIX) === 0) {
        if (groupLine.childNodes.length) { groupLine.appendChild(document.createTextNode("  ")); }
        groupLine.appendChild(document.createTextNode(g.name + ": "));
        groupLine.appendChild(el("strong", null, pct(g.probability)));
        groupLine.hidden = false;
      }
    });

    var rows = result.differential || [];
    var showAll = false;
    var body = r(container, "ranking");
    var toggle = r(container, "ranking-toggle");

    function drawRows() {
      body.textContent = "";
      (showAll ? rows : rows.slice(0, DIFFERENTIAL_ROWS)).forEach(function (row, index) {
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
      toggle.hidden = rows.length <= DIFFERENTIAL_ROWS;
      toggle.textContent = showAll ? "Show top " + DIFFERENTIAL_ROWS + " only"
                                   : "Show all " + rows.length + " conditions";
    }
    toggle.addEventListener("click", function () { showAll = !showAll; drawRows(); });
    drawRows();
  }

  /* ---------------- attention ---------------- */

  function renderAttention(container, images, findingName) {
    App.mount(container, "tpl-attention");
    r(container, "overlay-class").textContent = findingName;
    App.imageUrl(images.view).then(function (u) { r(container, "view").src = u; }).catch(function () {});
    App.imageUrl(images.overlay).then(function (u) { r(container, "overlay").src = u; }).catch(function () {});
  }

  /* ---------------- review ---------------- */

  function verdictText(review) {
    if (review.verdict === "correct") { return "Confirmed: " + nameOf(review.label); }
    if (review.verdict === "corrected") { return "Corrected to " + nameOf(review.true_class); }
    return "Other diagnosis: " + review.other_label;
  }

  var names = {};
  function nameOf(cls) { return names[cls] || cls; }

  function renderReview(container, exam, onSaved) {
    App.mount(container, "tpl-review");
    var result = exam.result;
    var predicted = result.finding["class"];
    result.differential.forEach(function (d) { names[d["class"]] = d.name; });

    var done = r(container, "done");
    var form = r(container, "form");
    var latest = exam.reviews && exam.reviews.length ? exam.reviews[exam.reviews.length - 1] : null;

    function showDone() {
      r(container, "done-verdict").textContent = verdictText(latest);
      r(container, "done-box").classList.toggle("is-disagreement", latest.verdict !== "correct");
      r(container, "done-comment").textContent = latest.comment || "";
      r(container, "done-comment").hidden = !latest.comment;
      r(container, "done-meta").textContent = "Reviewed by " + latest.reviewer + ", " +
        App.formatDate(latest.created_at);
      var history = r(container, "history");
      history.textContent = "";
      exam.reviews.slice(0, -1).reverse().forEach(function (old) {
        history.appendChild(el("li", null, "Earlier: " + verdictText(old) + ", by " +
          old.reviewer + ", " + App.formatDate(old.created_at)));
      });
      done.hidden = false;
      form.hidden = true;
    }

    function showForm(cancellable) {
      done.hidden = true;
      form.hidden = false;
      r(container, "cancel").hidden = !cancellable;
      App.showError(r(container, "error"), "");
    }

    /* Options: the prediction first and pre selected, then every other
       condition in order of probability, then a free text "other". */
    var optionsBox = r(container, "options");
    var otherInput;
    result.differential.forEach(function (row, index) {
      var label = el("label", "option-row" + (index === 0 ? " is-selected" : ""));
      var radio = el("input");
      radio.type = "radio";
      radio.name = "actual";
      radio.value = row["class"];
      radio.checked = index === 0;
      label.appendChild(radio);
      label.appendChild(el("span", "option-name", row.name));
      if (row["class"] === predicted) { label.appendChild(el("span", "option-tag", "Predicted")); }
      label.appendChild(el("span", "option-prob", pct(row.probability)));
      optionsBox.appendChild(label);
    });

    var otherLabel = el("label", "option-row option-other");
    var otherRadio = el("input");
    otherRadio.type = "radio";
    otherRadio.name = "actual";
    otherRadio.value = "__other__";
    otherLabel.appendChild(otherRadio);
    otherLabel.appendChild(el("span", "option-name", "Other diagnosis, not listed"));
    /* Always enabled: clinicians click straight into the box to type, and a
       disabled field would silently ignore that click. Focusing or typing in
       it selects "other". */
    otherInput = el("input", "input");
    otherInput.type = "text";
    otherInput.maxLength = 200;
    otherInput.placeholder = "Type the diagnosis, e.g. Otomycosis";
    otherLabel.appendChild(otherInput);
    optionsBox.appendChild(otherLabel);

    function markSelected() {
      var chosen = optionsBox.querySelector("input[name=actual]:checked");
      Array.prototype.forEach.call(optionsBox.querySelectorAll(".option-row"), function (row) {
        row.classList.toggle("is-selected", row.contains(chosen));
      });
    }

    optionsBox.addEventListener("change", function (e) {
      markSelected();
      if (e.target === otherRadio) { otherInput.focus(); }
    });
    ["focus", "input"].forEach(function (name) {
      otherInput.addEventListener(name, function () {
        if (!otherRadio.checked) {
          otherRadio.checked = true;
          markSelected();
        }
      });
    });

    var confirmBtn = r(container, "confirm");
    confirmBtn.textContent = "✓  Confirm: " + result.finding.name;

    function submit(payload) {
      var buttons = [confirmBtn, r(container, "save")];
      buttons.forEach(function (b) { b.disabled = true; });
      App.showError(r(container, "error"), "");
      payload.comment = r(container, "comment").value;
      App.request("POST", "/examinations/" + exam.id + "/review", { json: payload })
        .then(function (detail) {
          exam = detail;
          latest = detail.reviews[detail.reviews.length - 1];
          showDone();
          App.toast("Review saved.");
          if (onSaved) { onSaved(detail); }
        })
        .catch(function (err) { App.showError(r(container, "error"), err.message); })
        .finally(function () { buttons.forEach(function (b) { b.disabled = false; }); });
    }

    confirmBtn.addEventListener("click", function () { submit({ verdict: "correct" }); });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var chosen = optionsBox.querySelector("input[name=actual]:checked");
      if (!chosen) { return; }
      if (chosen.value === "__other__") {
        var text = otherInput.value.trim();
        if (text.length < 2) {
          App.showError(r(container, "error"), "Type the diagnosis, or choose a condition from the list.");
          return;
        }
        submit({ verdict: "other", other_label: text });
      } else if (chosen.value === predicted) {
        submit({ verdict: "correct" });
      } else {
        submit({ verdict: "corrected", true_class: chosen.value });
      }
    });

    r(container, "change").addEventListener("click", function () { showForm(true); });
    r(container, "cancel").addEventListener("click", showDone);

    if (latest) { showDone(); } else { showForm(false); }
  }

  App.result = {
    renderAssessment: renderAssessment,
    renderAttention: renderAttention,
    renderReview: renderReview
  };
})(window.App);
