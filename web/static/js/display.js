// Public kiosk display of one station (read only).
// The display link has the form /display#<token>: the fragment is never sent to servers or proxies;
// the token only travels to the API as a header.
// Principle: with missing or stale data NEVER show "free".
(function () {
  "use strict";

  const TOKEN = decodeURIComponent(location.hash.replace(/^#/, "")).trim();
  const SYMBOL = { free: "✓", occupied: "■", unknown: "?" };
  const params = new URLSearchParams(location.search);

  let lang = pickLang();
  let pollMs = 2000;
  let staleAfterS = 30;
  let last = null;
  let lastOkAt = null;
  let invalid = false;

  function pickLang() {
    const q = params.get("lang");
    if (q && window.I18N[q]) return q;
    try {
      const saved = localStorage.getItem("lang");
      if (saved && window.I18N[saved]) return saved;
    } catch (_) {}
    const nav = (navigator.language || "en").slice(0, 2).toLowerCase();
    return window.I18N[nav] ? nav : "en";
  }

  const t = (key, ...args) => {
    const v = window.I18N[lang][key] ?? window.I18N.en[key] ?? key;
    return typeof v === "function" ? v(...args) : v;
  };
  const fmtTime = (iso) =>
    iso ? new Date(iso).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–";

  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== undefined && v !== null) e.setAttribute(k === "class" ? "class" : k, v);
    for (const c of children) if (c !== null && c !== undefined) e.append(c);
    return e;
  }

  function applyLang() {
    document.documentElement.lang = lang;
    document.querySelectorAll("[data-i18n]").forEach((n) => (n.textContent = t(n.dataset.i18n)));
    document.querySelectorAll("[data-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
    const qr = document.getElementById("phone-qr");
    if (qr.getAttribute("src")) qr.alt = t("phoneQrAlt");
    render();
  }
  document.querySelectorAll("[data-lang]").forEach((b) =>
    b.addEventListener("click", () => {
      lang = b.dataset.lang;
      try { localStorage.setItem("lang", lang); } catch (_) {}
      applyLang();
    }));

  async function poll() {
    if (!TOKEN) { invalid = true; render(); return; }
    try {
      const r = await fetch("/api/v1/public/display/status", { cache: "no-store", credentials: "omit", headers: { "X-Display-Token": TOKEN } });
      if (r.status === 404) { invalid = true; last = null; }
      else if (r.ok) {
        invalid = false;
        last = await r.json();
        lastOkAt = Date.now();
        pollMs = Math.max(1000, (last.poll_interval_s || 2) * 1000);
        staleAfterS = last.stale_after_s || 30;
      }
    } catch (_) { /* the error becomes visible, no frozen state */ }
    render();
    setTimeout(poll, invalid ? 30000 : pollMs);
  }

  function render() {
    document.getElementById("invalid").hidden = !invalid;
    const ageS = lastOkAt ? Math.round((Date.now() - lastOkAt) / 1000) : null;
    const connOk = ageS !== null && ageS * 1000 <= pollMs * 2 + 1500;
    const stale = ageS === null || ageS > staleAfterS;
    const conn = document.getElementById("connection");
    conn.textContent = connOk ? t("connOk") : stale ? t("connLostStale") : t("connLost", ageS);
    conn.className = connOk ? "ok" : "error";
    if (last) {
      document.getElementById("station-name").textContent = last.display_name;
      document.title = last.display_name;
    }
    const slots = !last ? [] : stale ? last.slots.map((s) => ({ ...s, state: "unknown", unknown_reason: "connection" })) : last.slots;
    const free = slots.filter((s) => s.state === "free");
    const rec = stale || !last ? null : slots.find((s) => s.slot_id === last.recommendation && s.state === "free");
    document.getElementById("free-count").textContent = t("freeCount", free.length, slots.length || "–");
    document.getElementById("recommendation").textContent = rec ? t("recommendation", rec.label) : t("noRecommendation");
    document.getElementById("updated").textContent = t("updated", fmtTime(last && last.server_time));
    document.getElementById("simulated").hidden = !(last && last.simulated_data);

    document.getElementById("slots").replaceChildren(...slots.map((s) => {
      const isRec = rec && s.slot_id === rec.slot_id;
      const reason = s.state === "unknown" && s.unknown_reason ? t("reason_" + s.unknown_reason) : null;
      return el("li", { class: `slot ${s.state}${isRec ? " recommended" : ""}${s.alert ? " alerting" : ""}` },
        el("span", { class: "slot-label" }, s.label),
        el("span", { class: "slot-state" }, el("span", { class: "sym", "aria-hidden": "true" }, SYMBOL[s.state]), " ", t(s.state)),
        reason ? el("span", { class: "slot-reason" }, reason) : null,
        isRec ? el("span", { class: "badge rec" }, "→ " + t("recommended")) : null,
        s.alert ? el("span", { class: "badge warn" }, "⚠ " + t("alertMovement")) : null,
        el("span", { class: "slot-time small" }, fmtTime(s.last_update)));
    }));

    const alerts = document.getElementById("alerts");
    const active = slots.filter((s) => s.alert);
    const text = active.map((s) => t("alertText", s.label, fmtTime(s.alert.occurred_at))).join("\n");
    if (alerts.dataset.text !== text) {
      alerts.dataset.text = text; // only update on change (screen readers)
      alerts.replaceChildren(...active.map((s) => el("p", { class: "alert" }, "⚠ " + t("alertText", s.label, fmtTime(s.alert.occurred_at)) +
        (s.alert.simulated ? ` [${t("simulatedShort")}]` : ""))));
    }
  }

  // QR code of this display link, so passers-by can open the same view on their phone.
  let qrLoaded = false;
  async function loadQr() {
    if (qrLoaded || invalid || !TOKEN) return;
    try {
      const r = await fetch("/api/v1/public/display/qr", { cache: "no-store", credentials: "omit", headers: { "X-Display-Token": TOKEN } });
      if (!r.ok) return;
      const data = await r.json();
      const img = document.getElementById("phone-qr");
      img.src = data.qr;
      img.alt = t("phoneQrAlt");
      document.getElementById("phone").hidden = params.get("qr") === "0";
      qrLoaded = true;
    } catch (_) { /* optional */ }
  }
  setTimeout(loadQr, 1500);

  // new display link (different fragment) -> reload
  window.addEventListener("hashchange", () => location.reload());
  setInterval(render, 1000);
  applyLang();
  poll();
})();
