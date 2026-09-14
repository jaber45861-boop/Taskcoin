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
      card.setAttribute("data-method", m.code || "");
      card.setAttribute("data-method-ar", m.name_ar || m.name || "");

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
    // Method card selection → amount view
    var methodsHost = document.getElementById("withdraw-methods");
    if (methodsHost) {
      methodsHost.addEventListener("click", function (e) {
        var card = e.target.closest(".withdraw__method");
        if (!card) return;
        withdrawState.method = card.getAttribute("data-method");
        withdrawState.methodAr = card.getAttribute("data-method-ar");
        withdrawState.amountCents = null;
        var hint = document.getElementById("withdraw-amount-hint");
        if (hint) hint.textContent = "طريقة السحب: " + withdrawState.methodAr;
        switchView("withdraw-amount");
      });
    }
  }

  // ── Withdraw amount (state + validation) ────────────────────
  var withdrawState = {
    method: null,
    methodAr: null,
    amountCents: null,
    usdtAmount: null,
    networkCode: null,
    destination: null
  };

  function _isVodafone() { return withdrawState.method === "vodafone"; }
  function _isUsdt()     { return withdrawState.method === "usdt"; }

  function _validateVodafoneAccount(dest) {
    if (!dest) return false;
    var cleaned = dest.replace(/\s/g, "").replace(/-/g, "").replace(/\+/g, "");
    if (!/^\d+$/.test(cleaned)) return false;
    // 11 digits: 01 + 9 digits  OR  10 digits: 1 + 9 digits (without leading 0)
    return (cleaned.length === 11 && cleaned.indexOf("01") === 0) ||
           (cleaned.length === 10 && cleaned.charAt(0) === "1");
  }

  function _validateUsdtAddress(dest) {
    return /^0x[0-9a-fA-F]{40}$/.test(dest);
  }

  function validateWithdrawAmount(rawAmount) {
    // USDT: client-side only — preserve raw input exactly as entered
    if (_isUsdt()) {
      try {
        var usdtAmt = parseFloat(rawAmount);
        if (isNaN(usdtAmt) || usdtAmt < 0.15) {
          return Promise.resolve({ error: "invalid_amount" });
        }
        return Promise.resolve({ valid: true, usdtAmount: rawAmount });
      } catch (e) {
        return Promise.resolve({ error: "invalid_amount" });
      }
    }
    // Vodafone: server validates EGP parsing, min, and balance
    return fetch("/api/withdraw/validate-amount", {
      method: "POST",
      credentials: "include",
      headers: authHeaders(),
      body: JSON.stringify({ raw_amount: rawAmount }),
    })
      .then(function (res) {
        if (res.status === 401) {
          return { error: "unauthorized" };
        }
        if (res.status === 403) {
          return res.json().then(function (d) { return { error: d.error || "forbidden" }; });
        }
        if (res.status === 404) {
          return { error: "user_not_found" };
        }
        if (!res.ok) {
          return res.json().then(function (d) { return { error: d.error || "invalid_amount" }; });
        }
        return res.json();
      })
      .then(function (d) {
        if (d && d.ok) {
          return { valid: true, amountCents: d.amount_egp_cents };
        }
        return { error: (d && d.error) || "invalid_amount" };
      })
      .catch(function () {
        return { error: "network_error" };
      });
  }

  function bindWithdrawAmount() {
    var next = document.getElementById("withdraw-amount-next");
    var back = document.getElementById("withdraw-amount-back");
    var input = document.getElementById("withdraw-amount-input");
    var err = document.getElementById("withdraw-amount-error");
    var hint = document.getElementById("withdraw-amount-hint");

    if (hint && withdrawState.methodAr) {
      hint.textContent = "طريقة السحب: " + withdrawState.methodAr;
    }

    if (next) {
      next.addEventListener("click", function () {
        var raw = (input.value || "").trim();
        if (!raw) {
          err.textContent = "⚠️ أدخل مبلطاً صحيحاً.";
          return;
        }
        err.textContent = "";
        next.disabled = true;
        validateWithdrawAmount(raw).then(function (result) {
          next.disabled = false;
          if (result && result.valid) {
            if (result.amountCents !== undefined) withdrawState.amountCents = result.amountCents;
            if (result.usdtAmount !== undefined)   withdrawState.usdtAmount = result.usdtAmount;
            // Update account hint based on method
            var acctHint = document.getElementById("withdraw-account-hint");
            var acctInput = document.getElementById("withdraw-account-input");
            if (_isVodafone()) {
              if (acctHint) acctHint.textContent = "أدخل رقم محفظة Vodafone Cash (11 رقم يبدأ بـ 01)";
              if (acctInput) acctInput.placeholder = "01XXXXXXXXX";
            } else {
              if (acctHint) acctHint.textContent = "أدخل عنوان محفظتك على BEP-20 (يبدأ بـ 0x)";
              if (acctInput) acctInput.placeholder = "0x...";
            }
            switchView("withdraw-account");
          } else {
            err.textContent = withdrawAmountErrorText(result.error);
          }
        });
      });
    }

    if (back) {
      back.addEventListener("click", function () {
        input.value = "";
        err.textContent = "";
        switchView("withdraw");
      });
    }

    if (input) {
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); next.click(); }
      });
    }
  }

  function withdrawAmountErrorText(code) {
    switch (code) {
      case "invalid_amount":
        return "⚠️ المبلغ غير صحيح أو أقل من الحد الأدنى.";
      case "insufficient_balance":
        return "⚠️ الرصيد الحالي لا يكفي ل سحب المبلغ.";
      case "withdrawal_blocked":
        return "⚠️ السحب ممنوع على حسابك.";
      case "account_inactive":
        return "⚠️ الحساب غير نشط.";
      case "user_not_found":
        return "⚠️ لا يوجد مستخدم.";
      case "settings_unavailable":
        return "⚠️ لا يمكن تحديد الحد الأدنى حاليًا.";
      case "unauthorized":
        return "⚠️ يرجى تسجيل الدخول أولاً.";
      case "network_error":
        return "⚠️ فشل الاتصال بالخادم.";
      default:
        return "⚠️ حدث خطأ غير معروف.";
    }
  }

  // ── Withdraw account + final submission ────────────────────
  function bindWithdrawAccount() {
    var submitBtn = document.getElementById("withdraw-account-submit");
    var back = document.getElementById("withdraw-account-back");
    var input = document.getElementById("withdraw-account-input");
    var err = document.getElementById("withdraw-account-error");
    var hint = document.getElementById("withdraw-account-hint");

    if (hint && withdrawState.methodAr) {
      hint.textContent = "طريقة السحب: " + withdrawState.methodAr;
    }

    if (submitBtn) {
      submitBtn.addEventListener("click", function () {
        var dest = (input.value || "").trim();
        err.textContent = "";

        if (!dest || dest.length > 250) {
          err.textContent = "⚠️ أرسل رقم المحفظة أو الحساب بشكل صحيح (بحد أقصى 250 حرفاً).";
          return;
        }

        // Validate format matching bot's validate_vodafone_destination / validate_usdt_bep20_address
        if (_isVodafone() && !_validateVodafoneAccount(dest)) {
          err.textContent = "⚠️ رقم Vodafone غير صالح. أرسل 11 رقم يبدأ بـ 01.";
          return;
        }
        if (_isUsdt() && !_validateUsdtAddress(dest)) {
          err.textContent = "⚠️ عنوان BEP-20 غير صالح. يجب أن يبدأ بـ 0x ويكون 42 محرف hex.";
          return;
        }

        withdrawState.destination = dest;
        submitBtn.disabled = true;
        submitBtn.textContent = "⏳ جاري الإرسال...";

        submitWithdrawRequest().then(function (result) {
          submitBtn.disabled = false;
          submitBtn.textContent = "✅ إرسال الطلب";
          if (result && result.ok) {
            showWithdrawSuccess();
          } else {
            err.textContent = withdrawSubmitErrorText(result.error);
          }
        });
      });
    }

    if (back) {
      back.addEventListener("click", function () {
        input.value = "";
        err.textContent = "";
        // Go back to the correct amount view based on method
        if (_isUsdt()) {
          switchView("withdraw-usdt-amount");
        } else {
          switchView("withdraw-amount");
        }
      });
    }

    if (input) {
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && submitBtn && !submitBtn.disabled) { e.preventDefault(); submitBtn.click(); }
      });
    }
  }

  /** Submit withdrawal request matching create_v2_withdrawal_request semantics exactly.
   *  Vodafone → requested_egp_cents (EGP cents, integer from /api/withdraw/validate-amount)
   *  USDT     → usdt_amount (raw user input string, no conversion)
   *  USDT     → network_code: "BSC_BEP20" */
  function submitWithdrawRequest() {
    var body = {
      method_code: withdrawState.method,
      destination: withdrawState.destination
    };

    if (_isVodafone()) {
      body.requested_egp_cents = withdrawState.amountCents;
    } else if (_isUsdt()) {
      body.usdt_amount = withdrawState.usdtAmount;
      body.network_code = "BSC_BEP20";
    }

    return fetch("/api/withdraw", {
      method: "POST",
      credentials: "include",
      headers: authHeaders(),
      body: JSON.stringify(body),
    })
      .then(function (res) {
        return res.json().then(function (d) {
          if (res.ok && d.ok) return { ok: true, requestId: d.request_id };
          return { error: d.error || "unknown_error" };
        });
      })
      .catch(function () {
        return { error: "network_error" };
      });
  }

  function withdrawSubmitErrorText(code) {
    switch (code) {
      case "method_not_supported":
        return "⚠️ طريقة السحب غير مدعومة.";
      case "destination_invalid":
        return "⚠️ بيانات الوجهة غير صالحة.";
      case "below_minimum":
        return "⚠️ المبلغ أقل من الحد الأدنى المسموح.";
      case "insufficient_balance":
        return "❌ رصيدك غير كافٍ لإتمام هذا السحب.";
      case "cooldown":
        return "⏳ لقد استخدمت طلب السحب بالفعل. يمكنك طلب سحب جديد لاحقاً.";
      case "rate_unavailable":
        return "⚠️ تعذر الحصول على سعر صرف محدّث. حاول لاحقاً.";
      case "fraud":
        return "🚫 تم إيقاف طلب السحب مؤقتاً.";
      case "verification_unavailable":
        return "⚠️ تعذر التحقق من بعض الإحالات. حاول لاحقاً.";
      case "user_not_found":
        return "⚠️ المستخدم غير موجود.";
      case "withdrawal_blocked":
        return "⚠️ السحب ممنوع على حسابك.";
      case "account_inactive":
        return "⚠️ الحساب غير نشط.";
      case "invalid_usdt_amount":
        return "⚠️ كمية USDT غير صالحة.";
      case "invalid_egp_amount":
        return "⚠️ مبلغ EGP غير صالح.";
      case "unauthorized":
        return "⚠️ يرجى تسجيل الدخول أولاً.";
      case "network_error":
        return "⚠️ فشل الاتصال بالخادم. حاول مرة أخرى.";
      default:
        return "⚠️ تعذر إنشاء طلب السحب. حاول لاحقاً.";
    }
  }

  function showWithdrawSuccess() {
    var host = document.getElementById("view-withdraw-account");
    if (!host) {
      switchView("home");
      return;
    }
    host.innerHTML =
      '<div class="placeholder card">' +
      '  <div class="placeholder__icon">✅</div>' +
      '  <h2 class="placeholder__title">تم استلام طلب السحب</h2>' +
      '  <p class="placeholder__text">سيتم مراجعته وتحويل المبلغ خلال 24 ساعة كحد أقصى.</p>' +
      '</div>' +
      '<button class="balance__btn" id="withdraw-success-back" type="button">🏠 الرئيسية</button>';
    var backBtn = document.getElementById("withdraw-success-back");
    if (backBtn) backBtn.addEventListener("click", function () { switchView("home"); });
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
    bindWithdrawAmount();
    bindWithdrawAccount();
    bindTelegramTheme();
})();
