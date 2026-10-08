/* Administration: users, model versions and retraining, audit log. */

(function (App) {
  "use strict";

  var r = App.r, el = App.el, pct = App.pct;
  var pollTimer = null;

  function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }

  function randomPassword() {
    var alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
    var bytes = new Uint32Array(12);
    window.crypto.getRandomValues(bytes);
    var out = "";
    for (var i = 0; i < bytes.length; i++) { out += alphabet[bytes[i] % alphabet.length]; }
    return out.slice(0, 4) + "-" + out.slice(4, 8) + "-" + out.slice(8, 12);
  }

  /* ======================= Users ======================= */

  function usersPanel(panel) {
    App.mount(panel, "tpl-admin-users");
    var form = r(panel, "add-user");

    r(panel, "generate").addEventListener("click", function () {
      form.elements.password.value = randomPassword();
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var payload = {
        username: form.elements.username.value.trim().toLowerCase(),
        full_name: form.elements.full_name.value.trim(),
        role: form.elements.role.value,
        password: form.elements.password.value
      };
      App.request("POST", "/admin/users", { json: payload }).then(function (data) {
        App.showError(r(panel, "error"), "");
        App.toast("Added " + data.user.full_name + ". Give them the temporary password in person.");
        form.reset();
        load();
      }).catch(function (err) { App.showError(r(panel, "error"), err.message); });
    });

    function load() {
      App.request("GET", "/admin/users").then(function (data) {
        var body = r(panel, "rows");
        body.textContent = "";
        data.items.forEach(function (u) {
          var tr = el("tr");
          tr.appendChild(el("td", null, u.full_name));
          tr.appendChild(el("td", "muted", u.username));

          var roleCell = el("td");
          var role = el("select", "input");
          role.style.width = "auto";
          [["clinician", "Clinician"], ["admin", "Administrator"]].forEach(function (o) {
            var opt = el("option", null, o[1]);
            opt.value = o[0];
            opt.selected = u.role === o[0];
            role.appendChild(opt);
          });
          role.disabled = u.id === App.state.user.id;
          role.addEventListener("change", function () {
            App.request("PATCH", "/admin/users/" + u.id, { json: { role: role.value } })
              .then(function () { App.toast("Role updated."); load(); })
              .catch(function (err) { App.toast(err.message, true); load(); });
          });
          roleCell.appendChild(role);
          tr.appendChild(roleCell);

          var statusCell = el("td");
          statusCell.appendChild(u.is_active ? App.pill("active", "Active") : App.pill("inactive", "Inactive"));
          if (u.locked) { statusCell.appendChild(el("span", "sub", "Locked after failed sign ins")); }
          if (u.must_change_password && u.is_active) { statusCell.appendChild(el("span", "sub", "Temporary password")); }
          tr.appendChild(statusCell);

          tr.appendChild(el("td", "muted", u.last_login_at ? App.formatDate(u.last_login_at) : "Never"));

          var actions = el("td");
          actions.style.whiteSpace = "nowrap";
          if (u.id !== App.state.user.id) {
            var toggle = el("button", "btn btn-small" + (u.is_active ? " btn-danger" : ""),
              u.is_active ? "Deactivate" : "Activate");
            toggle.type = "button";
            toggle.addEventListener("click", function () {
              if (u.is_active && !window.confirm("Deactivate " + u.full_name + "? They will be signed out at once.")) { return; }
              App.request("PATCH", "/admin/users/" + u.id, { json: { is_active: !u.is_active } })
                .then(function () { load(); })
                .catch(function (err) { App.toast(err.message, true); });
            });
            actions.appendChild(toggle);
          }
          var reset = el("button", "btn btn-small", "Reset password");
          reset.type = "button";
          reset.style.marginLeft = "6px";
          reset.addEventListener("click", function () {
            var temp = randomPassword();
            if (!window.confirm("Set a new temporary password for " + u.full_name + "?\n\n" + temp +
                "\n\nWrite it down and give it to them in person. They must change it at next sign in.")) { return; }
            App.request("POST", "/admin/users/" + u.id + "/reset-password", { json: { password: temp } })
              .then(function () { App.toast("Password reset for " + u.full_name + "."); load(); })
              .catch(function (err) { App.toast(err.message, true); });
          });
          actions.appendChild(reset);
          tr.appendChild(actions);
          body.appendChild(tr);
        });
      }).catch(function (err) { App.toast(err.message, true); });
    }
    load();
  }

  /* ======================= Models and retraining ======================= */

  function metricRow(table, label, current, candidate, higherIsBetter) {
    var tr = el("tr");
    tr.appendChild(el("td", null, label));
    tr.appendChild(el("td", null, current === null || current === undefined ? "n/a" : pct(current)));
    var cell = el("td", null, candidate === null || candidate === undefined ? "n/a" : pct(candidate));
    if (current !== null && current !== undefined && candidate !== null && candidate !== undefined) {
      var better = higherIsBetter ? candidate > current + 1e-9 : candidate < current - 1e-9;
      var worse = higherIsBetter ? candidate < current - 1e-9 : candidate > current + 1e-9;
      if (better) { cell.className = "better"; }
      if (worse) { cell.className = "worse"; }
    }
    tr.appendChild(cell);
    table.appendChild(tr);
  }

  function versionBlock(v, onChange) {
    var box = el("div");
    box.style.padding = "14px 0";
    box.style.borderBottom = "1px solid var(--line-soft)";

    var head = el("div");
    head.style.display = "flex";
    head.style.alignItems = "center";
    head.style.gap = "10px";
    head.appendChild(el("strong", null, "Version " + v.version));
    head.appendChild(App.pill(v.status, App.capitalise(v.status)));
    head.appendChild(el("span", "result-meta", "Created " + App.formatDate(v.created_at) +
      (v.approved_by ? ", approved by " + v.approved_by : "")));
    box.appendChild(head);
    if (v.notes) {
      var notes = el("p", "field-hint", v.notes);
      notes.style.margin = "6px 0 0";
      box.appendChild(notes);
    }

    var s = v.summary || {};
    if (s.comparison) {
      var table = el("table", "compare");
      var head2 = el("tr");
      ["", "Current model", "This candidate"].forEach(function (h) { head2.appendChild(el("th", null, h)); });
      table.appendChild(head2);
      var c = s.comparison;
      metricRow(table, "Sensitivity, frozen test set", c.current.test.sensitivity, c.candidate.test.sensitivity, true);
      metricRow(table, "Specificity, frozen test set", c.current.test.specificity, c.candidate.test.specificity, true);
      metricRow(table, "Macro F1, frozen test set", c.current.test.macro_f1, c.candidate.test.macro_f1, true);
      metricRow(table, "Accuracy, held out local cases", c.current.local.accuracy, c.candidate.local.accuracy, true);
      metricRow(table, "Sensitivity, held out local cases", c.current.local.sensitivity, c.candidate.local.sensitivity, true);
      box.appendChild(table);
      var note = el("p", "field-hint", (c.local_cases || 0) + " held out local cases, " +
        (s.reviews_used || 0) + " reviewed cases added to training.");
      note.style.margin = "6px 0 0";
      box.appendChild(note);
    } else if (s.sensitivity !== undefined) {
      box.appendChild(el("p", "field-hint", "Test set: sensitivity " + pct(s.sensitivity) +
        ", specificity " + pct(s.specificity) + ", AUROC " + (s.auroc ? s.auroc.toFixed(3) : "n/a") +
        ", " + (s.test_images || "?") + " images."));
    }

    if (s.gate) {
      var gate = el("div", "gate");
      gate.appendChild(el("strong", null, s.gate.passed ? "Passes the validation gate." :
        "Does not pass the validation gate, so it cannot go live."));
      if (s.gate.reasons && s.gate.reasons.length) {
        var ul = el("ul");
        s.gate.reasons.forEach(function (reason) { ul.appendChild(el("li", null, reason)); });
        gate.appendChild(ul);
      }
      box.appendChild(gate);
    }

    if (v.status === "candidate") {
      var actions = el("div", "review-actions");
      var approve = el("button", "btn btn-confirm", "Approve and put live");
      approve.type = "button";
      approve.disabled = !(s.gate && s.gate.passed);
      approve.addEventListener("click", function () {
        if (!window.confirm("Put version " + v.version + " into service for all users?")) { return; }
        approve.disabled = true;
        App.request("POST", "/admin/models/" + v.id + "/approve").then(function () {
          App.toast("Version " + v.version + " is now in service.");
          onChange();
        }).catch(function (err) { App.toast(err.message, true); approve.disabled = false; });
      });
      var reject = el("button", "btn btn-danger", "Reject");
      reject.type = "button";
      reject.addEventListener("click", function () {
        if (!window.confirm("Reject candidate " + v.version + "?")) { return; }
        App.request("POST", "/admin/models/" + v.id + "/reject").then(onChange)
          .catch(function (err) { App.toast(err.message, true); });
      });
      actions.appendChild(approve);
      actions.appendChild(reject);
      box.appendChild(actions);
    }
    return box;
  }

  function modelsPanel(panel) {
    App.mount(panel, "tpl-admin-models");

    function statBox(label, value, sub) {
      var box = el("div", "stat");
      box.appendChild(el("span", "stat-label", label));
      box.appendChild(el("span", "stat-value", value));
      box.appendChild(el("span", "stat-ci", sub || ""));
      return box;
    }

    function pollJob(jobId) {
      stopPolling();
      App.request("GET", "/admin/jobs/" + jobId).then(function (data) {
        var job = data.job;
        r(panel, "job").hidden = false;
        r(panel, "job-status").textContent = "Run " + job.id + ": " + job.status +
          ", started " + App.formatDate(job.started_at) + " by " + job.started_by +
          (job.message ? ". " + job.message : "");
        var log = r(panel, "job-log");
        log.textContent = data.log || "Waiting for output...";
        log.scrollTop = log.scrollHeight;
        if (job.status === "running") {
          pollTimer = setTimeout(function () { pollJob(jobId); }, 5000);
        } else {
          load();
        }
      }).catch(function () {});
    }

    function load() {
      App.request("GET", "/admin/models").then(function (data) {
        var ready = data.readiness;
        var stats = r(panel, "readiness");
        stats.textContent = "";
        stats.appendChild(statBox("Model in service", data.active, ""));
        stats.appendChild(statBox("Reviewed cases", ready.usable_reviews, "usable for training"));
        stats.appendChild(statBox("Needed", ready.minimum_reviews, "before retraining"));
        stats.appendChild(statBox("Training data", ready.dataset_available ? "Present" : "Missing",
          "original dataset on this computer"));

        var problems = r(panel, "problems");
        problems.textContent = ready.problems.join(" ");
        problems.hidden = ready.problems.length === 0;
        r(panel, "start").disabled = !ready.ready;

        var versions = r(panel, "versions");
        versions.textContent = "";
        data.versions.forEach(function (v) { versions.appendChild(versionBlock(v, load)); });

        var running = ready.running_job || (data.jobs[0] && data.jobs[0].status === "running" ? data.jobs[0] : null);
        if (running) {
          pollJob(running.id);
        } else if (data.jobs[0]) {
          r(panel, "job").hidden = false;
          var last = data.jobs[0];
          r(panel, "job-status").textContent = "Last run " + last.id + ": " + last.status + ", " +
            App.formatDate(last.finished_at || last.started_at) + (last.message ? ". " + last.message : "");
          r(panel, "job-log").hidden = true;
        }
      }).catch(function (err) { App.toast(err.message, true); });
    }

    r(panel, "start").addEventListener("click", function () {
      if (!window.confirm("Start retraining? It takes about 40 minutes and runs in the background. " +
          "The current model stays in service until you approve a new one.")) { return; }
      App.request("POST", "/admin/retrain").then(function (data) {
        App.toast("Retraining started.");
        r(panel, "job-log").hidden = false;
        pollJob(data.job.id);
        r(panel, "start").disabled = true;
      }).catch(function (err) { App.toast(err.message, true); });
    });

    load();
  }

  /* ======================= Records ======================= */

  function recordsPanel(panel) {
    App.mount(panel, "tpl-admin-records");
    var form = r(panel, "clear-form");
    var button = r(panel, "clear");
    var phrase = "";

    function statBox(label, value, sub) {
      var box = el("div", "stat");
      box.appendChild(el("span", "stat-label", label));
      box.appendChild(el("span", "stat-value", value));
      box.appendChild(el("span", "stat-ci", sub || ""));
      return box;
    }

    function load() {
      App.request("GET", "/admin/records").then(function (d) {
        phrase = d.confirm_phrase;
        r(panel, "phrase").textContent = phrase;
        var counts = r(panel, "counts");
        counts.textContent = "";
        counts.appendChild(statBox("Patients", d.patients, "record numbers"));
        counts.appendChild(statBox("Examinations", d.examinations, ""));
        counts.appendChild(statBox("Reviews", d.reviews, "including earlier verdicts"));
        counts.appendChild(statBox("Stored images", App.formatSize(d.image_bytes), d.image_files + " files"));
      }).catch(function (err) { App.toast(err.message, true); });
    }

    function refreshButton() {
      button.disabled = !(phrase && form.elements.confirm.value.trim() === phrase && form.elements.password.value);
    }
    form.addEventListener("input", refreshButton);

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (button.disabled) { return; }
      if (!window.confirm("Delete every patient record, examination, review and image? This cannot be undone.")) { return; }
      button.disabled = true;
      App.request("POST", "/admin/records/clear", {
        json: { confirm: form.elements.confirm.value.trim(), password: form.elements.password.value }
      }).then(function (d) {
        form.reset();
        App.showError(r(panel, "error"), "");
        App.toast("Cleared " + d.cleared.examinations + " examinations and " + d.cleared.patients + " patient records.");
        load();
      }).catch(function (err) {
        App.showError(r(panel, "error"), err.message);
        form.elements.password.value = "";
        refreshButton();
      });
    });

    load();
  }

  /* ======================= Audit ======================= */

  var ACTIONS = {
    login: "Signed in", login_failed: "Failed sign in", account_locked: "Account locked",
    password_changed: "Changed password", password_reset: "Password reset",
    user_created: "Created user", user_updated: "Updated user",
    examination_created: "Analysed examination", examination_reviewed: "Reviewed examination",
    retraining_started: "Started retraining", model_approved: "Approved model",
    model_rejected: "Rejected model", examination_deleted: "Deleted examination",
    records_cleared: "Cleared all records"
  };

  function auditPanel(panel) {
    App.mount(panel, "tpl-admin-audit");
    App.request("GET", "/admin/audit?limit=200").then(function (data) {
      var body = r(panel, "rows");
      data.items.forEach(function (e) {
        var tr = el("tr");
        tr.appendChild(el("td", null, App.formatDate(e.at)));
        tr.appendChild(el("td", null, e.user || (e.detail && e.detail.username) || "Unknown"));
        tr.appendChild(el("td", null, ACTIONS[e.action] || e.action));
        var item = e.entity ? e.entity.replace("_", " ") : "";
        if (e.entity_id) { item += " " + e.entity_id; }
        tr.appendChild(el("td", "muted", item));
        tr.appendChild(el("td", "muted", e.ip || ""));
        body.appendChild(tr);
      });
    }).catch(function (err) { App.toast(err.message, true); });
  }

  /* ======================= Shell ======================= */

  App.views.admin = function (view) {
    App.mount(view, "tpl-admin");
    var panel = r(view, "panel");
    var tabs = view.querySelectorAll("[data-tab]");
    var panels = { users: usersPanel, models: modelsPanel, records: recordsPanel, audit: auditPanel };

    function open(name) {
      stopPolling();
      Array.prototype.forEach.call(tabs, function (t) { t.classList.toggle("is-active", t.dataset.tab === name); });
      panels[name](panel);
    }
    Array.prototype.forEach.call(tabs, function (t) {
      t.addEventListener("click", function () { open(t.dataset.tab); });
    });
    open("users");
  };
  App.views.admin.title = ["Administration", "User accounts, model versions, records and the audit log."];
  App.views.admin.leave = stopPolling;
})(window.App);
