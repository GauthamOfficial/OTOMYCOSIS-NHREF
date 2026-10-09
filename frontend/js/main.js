/* Start up, sign in and routing. */

(function (App) {
  "use strict";

  var screens = {
    login: document.getElementById("screen-login"),
    password: document.getElementById("screen-password"),
    app: document.getElementById("screen-app")
  };
  var view = document.getElementById("view");
  var currentView = null;

  function showScreen(name) {
    Object.keys(screens).forEach(function (k) { screens[k].hidden = k !== name; });
  }

  /* ---------------- sign in ---------------- */

  var loginForm = document.getElementById("login-form");
  var loginError = document.getElementById("login-error");
  var googleBox = document.getElementById("login-google");
  var googleReady = false;

  function showLogin(message) {
    showScreen("login");
    App.showError(loginError, message || "");
    loginForm.reset();
    if (googleReady) {
      /* Google is the normal way in; the password form stays out of the way
         until someone says they cannot use it. */
      loginForm.hidden = true;
    } else {
      document.getElementById("login-username").focus();
    }
  }

  /* ---------------- Sign in with Google ---------------- */

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var tag = document.createElement("script");
      tag.src = src;
      tag.async = true;
      tag.onload = resolve;
      tag.onerror = function () { reject(new Error("blocked")); };
      document.head.appendChild(tag);
    });
  }

  function onGoogleCredential(response) {
    App.showError(loginError, "");
    App.googleLogin(response.credential)
      .then(enter)
      .catch(function (err) {
        App.showError(loginError, err.message);
        /* A refused Google account is usually one nobody has added yet, so
           offer the other way in rather than leaving a dead end. */
        loginForm.hidden = false;
      });
  }

  function setUpGoogle() {
    return App.request("GET", "/auth/config", { retry: false }).then(function (conf) {
      if (!conf.google_enabled) { return; }
      /* hl=en keeps the button in the language of the rest of the interface,
         whatever language this computer is set to. */
      return loadScript("https://accounts.google.com/gsi/client?hl=en").then(function () {
        if (!window.google || !window.google.accounts) { throw new Error("blocked"); }
        window.google.accounts.id.initialize({
          client_id: conf.google_client_id,
          callback: onGoogleCredential,
          auto_select: false,
          cancel_on_tap_outside: true,
          /* Google follows the computer's language by default, which would
             leave one button in a different language from the interface. */
          locale: "en"
        });
        window.google.accounts.id.renderButton(document.getElementById("login-google-button"), {
          theme: "outline", size: "large", width: 320, text: "signin_with",
          shape: "rectangular", logo_alignment: "center", locale: "en"
        });
        googleReady = true;
        googleBox.hidden = false;
        loginForm.hidden = true;
      }).catch(function () {
        /* No internet, or the page is blocked. Leave the password form in
           place and say why, rather than showing a button that cannot work. */
        App.showError(loginError, "Google sign in could not be loaded, so this computer "
          + "appears to be offline. Administrators can sign in with a password.");
      });
    }).catch(function () {});
  }

  document.getElementById("login-password-toggle").addEventListener("click", function () {
    loginForm.hidden = !loginForm.hidden;
    this.textContent = loginForm.hidden ? "Cannot use Google?" : "Hide password sign in";
    if (!loginForm.hidden) { document.getElementById("login-username").focus(); }
  });

  loginForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var button = document.getElementById("login-submit");
    var username = document.getElementById("login-username").value.trim();
    var password = document.getElementById("login-password").value;
    if (!username || !password) {
      App.showError(loginError, "Enter your email address and password.");
      return;
    }
    button.disabled = true;
    App.login(username, password)
      .then(enter)
      .catch(function (err) {
        App.showError(loginError, err.message);
        document.getElementById("login-password").value = "";
      })
      .finally(function () { button.disabled = false; });
  });

  function showPasswordChange() {
    showScreen("password");
    App.passwordForm(document.getElementById("forced-password-form"), function () {
      App.toast("Password set. Welcome.");
      enter(App.state.user);
    });
  }

  App.hooks.signedOut = function (message) {
    leaveView();
    view.textContent = "";
    showLogin(message);
  };
  App.hooks.passwordRequired = showPasswordChange;

  document.addEventListener("click", function (e) {
    var target = e.target.closest("[data-action]");
    if (!target) { return; }
    if (target.dataset.action === "logout") { App.logout(); }
    if (target.dataset.action === "print") { window.print(); }
  });

  /* ---------------- application ---------------- */

  function enter(user) {
    if (user.must_change_password) {
      showPasswordChange();
      return;
    }
    document.getElementById("user-name").textContent = user.full_name;
    document.getElementById("user-role").textContent = user.role === "admin" ? "Administrator" : "Clinician";
    Array.prototype.forEach.call(document.querySelectorAll("[data-admin-only]"), function (n) {
      n.hidden = user.role !== "admin";
    });
    App.request("GET", "/model").then(function (m) {
      document.getElementById("footer-model").textContent =
        "Model " + m.version + ". Research use only. Not a substitute for clinical judgement.";
    }).catch(function () {});
    showScreen("app");
    route();
  }

  function leaveView() {
    if (currentView && currentView.leave) { currentView.leave(); }
    App.releaseUrls();
  }

  function route() {
    if (!App.state.token) { return; }
    var hash = location.hash.replace(/^#\/?/, "");
    var parts = hash.split("/");
    var name = parts[0] || "screen";
    var arg = parts[1];

    if (!App.views[name] || (name === "admin" && App.state.user.role !== "admin")) {
      location.hash = "#/screen";
      return;
    }

    leaveView();
    currentView = App.views[name];
    var title = currentView.title || ["", ""];
    document.getElementById("page-title").textContent = title[0];
    document.getElementById("page-subtitle").textContent =
      name === "account" ? "Signed in as " + App.state.user.full_name + " (" + App.state.user.username + ")" : title[1];
    Array.prototype.forEach.call(document.querySelectorAll("[data-nav]"), function (a) {
      a.classList.toggle("is-active", a.dataset.nav === name || (name === "exam" && a.dataset.nav === "history"));
    });
    currentView(view, arg);
  }

  window.addEventListener("hashchange", route);

  /* Clicking the link for the page already shown changes no hash, so no
     hashchange fires. Re-render anyway: "Screening" after finishing an
     examination must open a fresh form for the next patient. */
  document.addEventListener("click", function (e) {
    var link = e.target.closest("a[href^='#/']");
    if (link && link.getAttribute("href") === location.hash) {
      e.preventDefault();
      route();
      window.scrollTo(0, 0);
    }
  });

  /* ---------------- start ---------------- */

  /* A reload keeps the session through the refresh cookie. */
  App.refresh().then(function (ok) {
    if (ok) { enter(App.state.user); return; }
    /* The sign in button is only set up when nobody is signed in already. */
    setUpGoogle().then(function () { showLogin(""); });
  });
})(window.App);
