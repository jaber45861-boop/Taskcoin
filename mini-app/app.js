/* Taskcoin Mini App — Dashboard
   Static demo data for presentation only. No bot API calls, no network
   requests, no database access. Telegram theme sync only. */

(function () {
  "use strict";

  // ── Static demo data (display only) ──────────────────────
var DEMO = {
    quickActions: [
      { icon: "🎯", label: "المهام",     nav: "tasks" },
      { icon: "👥", label: "invitation صديق", nav: "invite" },
      { icon: "🎁", label: "المكافآت",  nav: "home" },
    ],
  };

  // ── Renderers ────────────────────────────────────────────
  function renderBalance() {
    var amount = document.getElementById("balance-amount");
    if (amount) {
      amount.innerHTML = "";
      amount.appendChild(document.createTextNode("0.00"));
    }
  }

  function renderQuickActions() {
    var host = document.getElementById("quick-actions");
    if (!host) return;
    host.innerHTML = "";
    DEMO.quickActions.forEach(function (a) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "quick__item";
      if (a.nav && a.nav !== "home") btn.setAttribute("data-nav-jump", a.nav);

      var icon = document.createElement("span");
      icon.className = "quick__icon";
      icon.textContent = a.icon;

      var label = document.createElement("span");
      label.className = "quick__label";
      label.textContent = a.label;

      btn.appendChild(icon);
      btn.appendChild(label);
      host.appendChild(btn);
    });
  }

  function renderActivity() {
    var host = document.getElementById("activity-list");
    if (!host) return;
    host.innerHTML =
      '<p class="placeholder__text">لا توجد نشاطات حتى الآن.</p>';
  }

  // ── Bottom navigation ────────────────────────────────────
  function switchView(name) {
    var views = document.querySelectorAll(".view");
    for (var i = 0; i < views.length; i++) {
      views[i].classList.toggle("is-active", views[i].getAttribute("data-view") === name);
    }
    var buttons = document.querySelectorAll(".bottom-nav__item");
    for (var j = 0; j < buttons.length; j++) {
      buttons[j].classList.toggle("is-active", buttons[j].getAttribute("data-nav") === name);
    }
    if (name === "profile") renderProfile();
    if (name === "withdraw") renderWithdrawMethods();
    window.scrollTo({ top: 0 });
  }

  function bindNavigation() {
    var nav = document.getElementById("bottom-nav");
    if (nav) {
      nav.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-nav]");
        if (btn) switchView(btn.getAttribute("data-nav"));
      });
    }
    // Quick-action buttons can jump to a tab.
    var quick = document.getElementById("quick-actions");
    if (quick) {
      quick.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-nav-jump]");
        if (btn) switchView(btn.getAttribute("data-nav-jump"));
      });
    }
  }

  // ── Session auth ────────────────────────────────────────
  var sessionToken = null;
  var sessionReady = null; // Promise that resolves when session is ready

  function initSession() {
    var tg = window.Telegram && window.Telegram.WebApp;
    var initData = tg && tg.initData;
    if (!initData) {
      sessionReady = Promise.resolve(null);
      return sessionReady;
    }
    sessionReady = fetch("/api/rewards/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: initData }),
    })
      .then(function (res) {
        if (!res.ok) throw res.status;
        return res.json();
      })
      .then(function (data) {
        if (data.ok && data.session_token) {
          sessionToken = data.session_token;
        }
        return sessionToken;
      })
      .catch(function () {
        return null;
      });
    return sessionReady;
  }

  function authHeaders() {
    var h = { "Content-Type": "application/json" };
    if (sessionToken) h["Authorization"] = "Bearer " + sessionToken;
    return h;
  }

  // ── Profile (live data from /api/profile) ──────────────
  var profileCache = null;
  var profileLoading = false;

  function renderProfile() {
    var host = document.getElementById("view-profile");
    if (!host) return;

    // Already rendered with live data — skip
    if (profileCache && host.querySelector(".profile__name")) return;

    // Show loading state
    host.innerHTML =
      '<div class="placeholder card">' +
      '<div class="placeholder__icon">👤</div>' +
      '<h2 class="placeholder__title">جاري التحميل…</h2>' +
      '</div>';

    if (profileLoading) return;
    profileLoading = true;

    var fetchProfile = function () {
      return fetch("/api/profile", {
        credentials: "include",
        headers: authHeaders(),
      })
        .then(function (res) {
          if (!res.ok) throw res.status;
          return res.json();
        })
        .then(function (data) {
          profileCache = data;
          profileLoading = false;
          _paintProfile(host, data);
        })
        .catch(function () {
          profileLoading = false;
          host.innerHTML =
            '<div class="placeholder card">' +
            '<div class="placeholder__icon">👤</div>' +
            '<h2 class="placeholder__title">حسابي</h2>' +
            '<p class="placeholder__text">تعذر تحميل البيانات. سجّل الدخول أولاً.</p>' +
            '</div>';
        });
    };

    if (sessionReady) {
      sessionReady.then(fetchProfile);
    } else {
      fetchProfile();
    }
  }

  function _paintProfile(host, d) {
    var balance = typeof d.balance_usd === "number" ? d.balance_usd.toFixed(2) : "0.00";
    var username = d.username ? "@" + d.username : "—";
    var fullName = d.full_name || d.first_name || "—";
    var joined = d.joined_at ? d.joined_at.slice(0, 10) : "—";

    host.innerHTML =
      '<div class="card profile">' +
      '  <div class="profile__avatar">👤</div>' +
      '  <h2 class="profile__name">' + _esc(fullName) + "</h2>" +
      '  <p class="profile__username">' + _esc(username) + "</p>" +
      "</div>" +
      '<div class="card profile">' +
      "  <div class=\"profile__row\">" +
      "    <span class=\"profile__label\">💰 الرصيد</span>" +
      "    <span class=\"profile__value\">$" + _esc(balance) + "</span>" +
      "  </div>" +
      "  <div class=\"profile__row\">" +
      "    <span class=\"profile__label\">👥 الإحالات</span>" +
      "    <span class=\"profile__value\">" + (d.referral_count || 0) + "</span>" +
      "  </div>" +
      "  <div class=\"profile__row\">" +
      "    <span class=\"profile__label\">📦 طلبات المتجر</span>" +
      "    <span class=\"profile__value\">" + (d.orders_count || 0) + "</span>" +
      "  </div>" +
      "  <div class=\"profile__row\">" +
      "    <span class=\"profile__label\">📅 تاريخ التسجيل</span>" +
      "    <span class=\"profile__value\">" + _esc(joined) + "</span>" +
      "  </div>" +
      "  <div class=\"profile__row\">" +
      "    <span class=\"profile__label\">🆔 المعرّف</span>" +
      "    <span class=\"profile__value\">" + d.user_id + "</span>" +
      "  </div>" +
      "</div>";
  }

  function _esc(s) {
    var el = document.createElement("span");
    el.textContent = s;
    return el.innerHTML;
  }

  // ── Withdraw methods ────────────────────────────────────────
  var withdrawCache = null;
  var withdrawLoading = false;

  function renderWithdrawMethods() {
    var host = document.getElementById("withdraw-methods");
    if (!host) return;

    if (withdrawCache && host.querySelector(".withdraw__method")) return;

    host.innerHTML =
      '<p class="placeholder__text">جاري التحميل…</p>';

    if (withdrawLoading) return;
    withdrawLoading = true;

    var fetchMethods = function () {
      return fetch("/api/withdraw/methods", {
        credentials: "include",
        headers: authHeaders(),
      })
        .then(function (res) {
          if (!res.ok) throw res.status;
          return res.json();
        })
        .then(function (data) {
          withdrawCache = data;
          withdrawLoading = false;
          _paintWithdrawMethods(host, data);
        })
        .catch(function () {
          withdrawLoading = false;
          host.innerHTML =
            '<p class="placeholder__text">تعذر تحميل طرق السحب. سجّل الدخول أولاً.</p>';
        });
    };

    if (sessionReady) {
      sessionReady.then(fetchMethods);
    } else {
      initSession().then(fetchMethods);
    }
  }

  function _paintWithdrawMethods(host, data) {
    var count = document.getElementById("withdraw-method-count");
    if (count) count.textContent = data.methods.length + " طرق";

    host.innerHTML = "";
    data.methods.forEach(function (m) {
      var card = document.createElement("div");
      card.className = "card withdraw__method";

      var icon = document.createElement("span");
      icon.className = "withdraw__icon";
      icon.textContent = m.type === "mobile_wallet" ? "📱" : "₿";

      var body = document.createElement("div");
      body.className = "withdraw__body";

      var name = document.createElement("h3");
      name.className = "withdraw__name";
      name.textContent = m.name_ar || m.name;

      var info = document.createElement("p");
      info.className = "withdraw__info";
      if (m.type === "mobile_wallet") {
        info.textContent = "الحد الأدنى: " + m.min_amount_egp_cents + "قرش";
      } else {
        info.textContent = "الحد الأدنى: " + m.min_amount_usdt + " USDT";
      }

      body.appendChild(name);
      body.appendChild(info);
      card.appendChild(icon);
      card.appendChild(body);
      host.appendChild(card);
    });
  }

  // ── Withdraw button ────────────────────────────────────────
  function bindWithdrawal() {
    var btn = document.getElementById("withdraw-btn");
    if (btn) {
      btn.addEventListener("click", function () {
        switchView("withdraw");
      });
    }
    var back = document.getElementById("withdraw-back");
    if (back) {
      back.addEventListener("click", function () {
        switchView("home");
      });
    }
  }

  // ── Telegram theme sync (presentation only) ──────────────
  function bindTelegramTheme() {
    var tg = window.Telegram && window.Telegram.WebApp;
    if (!tg) return; // opened in a plain browser — static defaults apply

    tg.ready();
    tg.expand();

    function themeColor(key, fallback) {
      var param = tg.themeParams && tg.themeParams[key];
      return typeof param === "string" && param ? param : fallback;
    }

    function applyTheme() {
      var root = document.documentElement.style;
      root.setProperty("--bg", themeColor("secondary_bg_color", "#0e1621"));
      root.setProperty("--surface", themeColor("bg_color", "#17212b"));
      root.setProperty("--surface-muted", themeColor("bg_color", "#1c2733"));
      root.setProperty("--border", themeColor("section_separator_color", "#243243"));
      root.setProperty("--text", themeColor("text_color", "#f5f5f5"));
      root.setProperty("--text-muted", themeColor("hint_color", "#8fa3b5"));
      root.setProperty("--accent", themeColor("button_color", "#f0b90b"));
      root.setProperty("--accent-text", themeColor("button_text_color", "#10161d"));
      try { tg.setHeaderColor("bg_color"); } catch (e) { /* older clients */ }
    }

    applyTheme();
    if (typeof tg.onEvent === "function") tg.onEvent("themeChanged", applyTheme);

    var badge = document.getElementById("connection-badge");
    var label = document.getElementById("connection-label");
    if (badge && label) {
      badge.hidden = false;
      label.textContent =
        tg.initDataUnsafe && tg.initDataUnsafe.user
          ? "مرحبًا، " + (tg.initDataUnsafe.user.first_name || "بالمستخدم")
          : "متصل";
    }
  }

  // ── Boot ─────────────────────────────────────────────────
  initSession();
  renderBalance();
  renderQuickActions();
  renderActivity();
bindNavigation();
    bindWithdrawal();
    bindTelegramTheme();
})();
