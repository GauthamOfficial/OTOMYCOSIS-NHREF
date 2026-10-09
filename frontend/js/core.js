/* NHREF OtoScan: session, API access and shared helpers.
   Nawaloka Hospital PLC Research and Education Foundation.

   The access token lives only in this script's memory, never in storage, so
   it disappears with the tab. A reload recovers the session through the
   refresh cookie, which page scripts cannot read. */

window.App = (function () {
  "use strict";

  var API = "/api/v1";
  var IDLE_MINUTES = 30;

  var state = {
    token: null,
    user: null,
    refreshTimer: null,
    refreshing: null,
    lastActivity: Date.now(),
    urls: []
  };

  var hooks = { signedOut: function () {}, passwordRequired: function () {} };

  /* ---------------- DOM helpers ---------------- */

  function r(root, name) { return root.querySelector('[data-r="' + name + '"]'); }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  function mount(container, templateId) {
    container.textContent = "";
    container.appendChild(document.getElementById(templateId).content.cloneNode(true));
    return container;
  }

  function pct(value, digits) {
    if (value === null || value === undefined || isNaN(value)) { return "n/a"; }
    var v = Math.max(0, Math.min(1, Number(value)));
    return (v * 100).toFixed(digits === undefined ? 1 : digits) + "%";
  }

  function formatDate(iso, withTime) {
    if (!iso) { return ""; }
    var d = new Date(iso);
    var opts = { day: "numeric", month: "short", year: "numeric" };
    if (withTime !== false) { opts.hour = "2-digit"; opts.minute = "2-digit"; }
    return d.toLocaleString(undefined, opts);
  }

  function formatSize(bytes) {
    if (bytes < 1024) { return bytes + " B"; }
    if (bytes < 1048576) { return (bytes / 1024).toFixed(0) + " KB"; }
    return (bytes / 1048576).toFixed(1) + " MB";
  }

  function capitalise(text) { return text ? text.charAt(0).toUpperCase() + text.slice(1) : ""; }

  var STATUS_TEXT = {
    unreviewed: "Awaiting review", confirmed: "Confirmed", corrected: "Corrected", other: "Other diagnosis"
  };

  function pill(kind, text) { return el("span", "pill pill-" + kind, text); }
  function statusPill(status) { return pill(status, STATUS_TEXT[status] || status); }
  function referralPill(rec) { return pill(rec, rec === "refer" ? "Refer" : "Routine"); }

  var toastTimer = null;
  function toast(message, isError) {
    var box = document.getElementById("toast");
    box.textContent = message;
    box.classList.toggle("is-error", !!isError);
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { box.hidden = true; }, 4200);
  }

  function showError(box, message) {
    if (!box) { return; }
    box.textContent = message || "";
    box.hidden = !message;
  }

  /* ---------------- session ---------------- */

  function readCookie(name) {
    var match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[1]) : "";
  }

  function scheduleRefresh(expiresIn) {
    clearTimeout(state.refreshTimer);
    var ms = Math.max(30, (expiresIn || 900) - 60) * 1000;
    state.refreshTimer = setTimeout(function () { refresh(); }, ms);
  }

  function adopt(data) {
    state.token = data.access_token;
    state.user = data.user;
    scheduleRefresh(data.expires_in);
    return data.user;
  }

  function clearSession() {
    state.token = null;
    state.user = null;
    clearTimeout(state.refreshTimer);
    releaseUrls();
  }

  /* One refresh at a time; concurrent callers share the same attempt. */
  function refresh() {
    if (state.refreshing) { return state.refreshing; }
    /* The CSRF cookie exists exactly when a refresh cookie does. Without it
       there is no session to restore, so skip a request that would only 401. */
    if (!readCookie("csrf_refresh_token")) { return Promise.resolve(false); }
    state.refreshing = fetch(API + "/auth/refresh", {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-TOKEN": readCookie("csrf_refresh_token") }
    }).then(function (response) {
      if (!response.ok) { return false; }
      return response.json().then(function (data) { adopt(data); return true; });
    }).catch(function () { return false; })
      .finally(function () { state.refreshing = null; });
    return state.refreshing;
  }

  function login(username, password) {
    return fetch(API + "/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: username, password: password })
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) { throw new Error(data.error || "Sign in failed."); }
        state.lastActivity = Date.now();
        return adopt(data);
      });
    });
  }

  function googleLogin(credential) {
    return fetch(API + "/auth/google", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential: credential })
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) { throw new Error(data.error || "Google sign in failed."); }
        state.lastActivity = Date.now();
        return adopt(data);
      });
    });
  }

  function logout(message) {
    var hadSession = !!state.token;
    clearSession();
    return fetch(API + "/auth/logout", { method: "POST", credentials: "same-origin" })
      .catch(function () {})
      .then(function () { hooks.signedOut(message || (hadSession ? "" : "")); });
  }

  /* ---------------- API ---------------- */

  function request(method, path, options) {
    options = options || {};
    var url = path.indexOf("/api/") === 0 ? path : API + path;
    var headers = {};
    var body;
    if (state.token) { headers.Authorization = "Bearer " + state.token; }
    if (options.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(options.json);
    } else if (options.form) {
      body = options.form;
    }

    return fetch(url, { method: method, headers: headers, body: body, credentials: "same-origin" })
      .then(function (response) {
        if (response.status === 401 && options.retry !== false) {
          return refresh().then(function (ok) {
            if (ok) { return request(method, path, Object.assign({}, options, { retry: false })); }
            logout("Your session has ended. Please sign in again.");
            throw new Error("Your session has ended.");
          });
        }
        if (response.status === 401) {
          logout("Your session has ended. Please sign in again.");
          throw new Error("Your session has ended.");
        }
        if (options.raw) { return response; }
        return response.json().catch(function () { return {}; }).then(function (data) {
          if (response.status === 403 && data.code === "password_change_required") {
            hooks.passwordRequired();
          }
          if (!response.ok) {
            var error = new Error(data.error || "Request failed (" + response.status + ").");
            error.status = response.status;
            error.data = data;
            throw error;
          }
          return data;
        });
      });
  }

  /* Images need the Authorization header, so they are fetched as blobs. */
  function imageUrl(path) {
    return request("GET", path, { raw: true }).then(function (response) {
      if (!response.ok) { throw new Error("Image could not be loaded."); }
      return response.blob();
    }).then(function (blob) {
      var url = URL.createObjectURL(blob);
      state.urls.push(url);
      return url;
    });
  }

  function releaseUrls() {
    state.urls.forEach(function (u) { URL.revokeObjectURL(u); });
    state.urls = [];
  }

  /* ---------------- inactivity ---------------- */

  ["mousemove", "keydown", "click", "touchstart", "scroll"].forEach(function (name) {
    window.addEventListener(name, function () { state.lastActivity = Date.now(); }, { passive: true });
  });

  setInterval(function () {
    if (state.token && Date.now() - state.lastActivity > IDLE_MINUTES * 60000) {
      logout("You were signed out after " + IDLE_MINUTES + " minutes of inactivity.");
    }
  }, 30000);

  return {
    state: state,
    hooks: hooks,
    r: r, el: el, mount: mount, pct: pct, formatDate: formatDate, formatSize: formatSize,
    capitalise: capitalise, pill: pill, statusPill: statusPill, referralPill: referralPill,
    STATUS_TEXT: STATUS_TEXT, toast: toast, showError: showError,
    login: login, googleLogin: googleLogin, logout: logout, refresh: refresh, request: request,
    imageUrl: imageUrl, releaseUrls: releaseUrls,
    views: {}
  };
})();
