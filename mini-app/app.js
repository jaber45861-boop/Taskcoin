/* Taskcoin Mini App — shell bootstrap
   Presentation only: adapts to the Telegram client theme and expands the
   viewport. No bot API calls, no network requests, no business logic. */

(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp;
  if (!tg) return; // opened in a plain browser — static defaults already apply

  // Announce the app is ready (removes Telegram's loading placeholder).
  tg.ready();
  tg.expand();

  // ── Telegram theme → CSS variables ────────────────────────
  function themeColor(cssVar, key, fallback) {
    var param = tg.themeParams && tg.themeParams[key];
    return typeof param === "string" && param ? param : fallback;
  }

  function applyTheme() {
    var root = document.documentElement.style;
    root.setProperty("--bg", themeColor("--bg", "secondary_bg_color", "#0e1621"));
    root.setProperty("--surface", themeColor("--surface", "bg_color", "#17212b"));
    root.setProperty(
      "--surface-muted",
      themeColor("--surface-muted", "bg_color", "#1c2733")
    );
    root.setProperty("--border", themeColor("--border", "section_separator_color", "#243243"));
    root.setProperty("--text", themeColor("--text", "text_color", "#f5f5f5"));
    root.setProperty(
      "--text-muted",
      themeColor("--text-muted", "hint_color", "#8fa3b5")
    );
    root.setProperty("--accent", themeColor("--accent", "button_color", "#f0b90b"));
    root.setProperty(
      "--accent-text",
      themeColor("--accent-text", "button_text_color", "#10161d")
    );

    var header = document.querySelector(".shell__header");
    if (header && typeof tg.setHeaderColor === "function") {
      try { tg.setHeaderColor("bg_color"); } catch (e) { /* older clients */ }
    }
  }

  applyTheme();
  if (typeof tg.onEvent === "function") {
    tg.onEvent("themeChanged", applyTheme);
  }

  // ── Connection badge (visual only) ───────────────────────
  var badge = document.getElementById("connection-badge");
  var label = document.getElementById("connection-label");
  if (badge && label) {
    badge.hidden = false;
    label.textContent = typeof tg.initDataUnsafe === "object" &&
      tg.initDataUnsafe && tg.initDataUnsafe.user
      ? "مرحبًا، " + (tg.initDataUnsafe.user.first_name || "بالمستخدم")
      : "متصل";
  }
})();
