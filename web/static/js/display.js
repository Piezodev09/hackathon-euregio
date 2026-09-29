// Öffentliche Kiosk-Anzeige EINES Stellplatzes (nur lesend) – z. B. auf dem Display oben vorne am Stellplatz.
// Der Anzeige-Link hat die Form /display#<token>: Das Fragment wird nie an Server oder Proxys
// übertragen; das Token geht nur als Header an die API.
// Grundsatz: Bei fehlenden, veralteten oder fehlerhaften Daten NIE "frei" anzeigen.
(function () {
  "use strict";

  const TOKEN = decodeURIComponent(location.hash.replace(/^#/, "")).trim();
  const params = new URLSearchParams(location.search);
  // Lokaler Modus: Anzeige wird vom Agent auf dem Raspberry Pi ausgeliefert (http://127.0.0.1:8088/local).
  // Der Agent liefert den Plattform-Status – oder bei Ausfall den Zustand direkt vom Sensor (gekennzeichnet).
  const LOCAL = location.pathname === "/local" || params.has("local");
  const NS = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"';
  const ICONS = {
    free: `<svg ${NS}><circle cx="24" cy="24" r="20" fill="none" stroke="currentColor" stroke-width="4"/><path d="M14 25l7 7 13-15" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    occupied: `<svg ${NS}><circle cx="12" cy="32" r="8" fill="none" stroke="currentColor" stroke-width="3.5"/><circle cx="36" cy="32" r="8" fill="none" stroke="currentColor" stroke-width="3.5"/><path d="M12 32l8-14h11l5 14M20 18l7 14h-15M31 18l-2-6h5M17 13h6" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    unknown: `<svg ${NS}><circle cx="24" cy="24" r="20" fill="none" stroke="currentColor" stroke-width="4" stroke-dasharray="7 4"/><path d="M18 19a6 6 0 1 1 8.4 5.5c-1.6.8-2.4 2-2.4 3.5v1.5" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><circle cx="24" cy="35.5" r="2.8" fill="currentColor"/></svg>`,
    reserved: `<svg ${NS}><circle cx="24" cy="27" r="16" fill="none" stroke="currentColor" stroke-width="4"/><path d="M24 18v9l6 5" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/><path d="M18 5h12M24 5v6" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>`,
    vib: `<svg ${NS}><path d="M4 24h7l4-10 6 20 6-24 6 20 4-6h7" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  };
  const parser = new DOMParser();
  const icon = (name, cls) => {
    const svg = document.importNode(parser.parseFromString(ICONS[name], "image/svg+xml").documentElement, true);
    svg.setAttribute("class", cls || "");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    return svg;
  };

  let lang = pickLang();
  let pollMs = 2000;
  let staleAfterS = 30;
  let last = null;
  let lastOkAt = null;
  let invalid = false;
  let shownState = null;
  let shownAlert = "";

  function pickLang() {
    const q = params.get("lang");
    if (q && window.I18N[q]) return q;
    try {
      const saved = localStorage.getItem("lang");
      if (saved && window.I18N[saved]) return saved;
    } catch (_) {}
    const nav = (navigator.language || "de").slice(0, 2);
    return window.I18N[nav] ? nav : "de";
  }

  const t = (key, ...args) => {
    const v = window.I18N[lang][key] ?? window.I18N.de[key] ?? key;
    return typeof v === "function" ? v(...args) : v;
  };
  const fmtTime = (iso) =>
    iso ? new Date(iso).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–";

  function applyLang() {
    document.documentElement.lang = lang;
    document.querySelectorAll("[data-i18n]").forEach((n) => (n.textContent = t(n.dataset.i18n)));
    document.querySelectorAll("[data-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
    shownState = null;
    shownAlert = "";
    render();
  }
  document.querySelectorAll("[data-lang]").forEach((b) =>
    b.addEventListener("click", () => {
      lang = b.dataset.lang;
      try { localStorage.setItem("lang", lang); } catch (_) {}
      applyLang();
    }));

  async function poll() {
    if (!TOKEN && !LOCAL) { invalid = true; render(); return; }
    try {
      const stall = params.get("stall");
      const r = LOCAL ? await fetch("/local/status" + (stall ? "?stall=" + encodeURIComponent(stall) : ""), { cache: "no-store" })
        : await fetch("/api/v1/public/display/status", { cache: "no-store", credentials: "omit", headers: { "X-Display-Token": TOKEN } });
      if (r.status === 404) { invalid = true; last = null; }
      else if (r.ok) {
        invalid = false;
        last = await r.json();
        lastOkAt = Date.now();
        pollMs = Math.max(1000, (last.poll_interval_s || 2) * 1000);
        staleAfterS = last.stale_after_s || 30;
      }
    } catch (_) { /* Fehler wird sichtbar gemacht, kein eingefrorener Status */ }
    render();
    setTimeout(poll, invalid ? 30000 : pollMs);
  }

  function render() {
    document.getElementById("invalid").hidden = !invalid;
    const ageS = lastOkAt ? Math.round((Date.now() - lastOkAt) / 1000) : null;
    const connOk = ageS !== null && ageS * 1000 <= pollMs * 2 + 1500;
    const connLost = ageS === null || ageS > staleAfterS;
    document.getElementById("connection").textContent = connOk ? t("connOk") : ageS === null ? "–" : t("connLost", ageS);
    if (last) {
      document.getElementById("station-name").textContent = last.display_name;
      document.getElementById("location").textContent = last.location || "";
      document.title = last.display_name;
    }

    // Zustand: nur aus aktueller, gültiger Antwort – sonst STATUS UNBEKANNT.
    let state = "unknown";
    let reason = "connection";
    if (last && !connLost && !invalid) {
      state = last.state;
      reason = last.state === "unknown" ? last.unknown_reason || "no_data" : null;
    }
    const mins = last && last.reservation ? Math.max(1, Math.ceil((new Date(last.reservation.until) - Date.now()) / 60000)) : 0;
    const sub = state === "unknown" ? t("reason_" + reason, staleAfterS) : state === "reserved" ? t("sub_reserved", mins) : t("sub_" + state);
    const box = document.getElementById("status");
    box.dataset.state = state;
    document.getElementById("status-word").textContent = t(state);
    document.getElementById("status-sub").textContent = sub;
    if (shownState !== state) {
      shownState = state;
      document.getElementById("status-icon").replaceChildren(icon(state, "sbb-status__icon"));
      document.getElementById("live").textContent = `${t(state)}. ${sub}`;
    }
    document.getElementById("simulated").hidden = !(last && last.simulated_data);
    document.getElementById("maint").hidden = !(last && last.maintenance && !connLost);
    const closed = !connLost && last && last.closed;
    const closedEl = document.getElementById("closed");
    closedEl.hidden = !closed;
    if (closed) {
      const opens = closed.opens_at ? new Date(closed.opens_at).toLocaleString(lang, { weekday: "short", hour: "2-digit", minute: "2-digit" }) : null;
      closedEl.textContent = (closed.reason === "closure" ? t("closed_closure", closed.note) : t("closed_hours")) + (opens ? " " + t("opens", opens) : "");
    }
    document.getElementById("offline").hidden = !(last && last.offline);
    document.getElementById("camera").hidden = !(last && last.camera_active);
    const eur = (c) => new Intl.NumberFormat(lang, { style: "currency", currency: "EUR" }).format((c || 0) / 100);
    const tap = !connLost && last && last.last_tap;
    const tapEl = document.getElementById("tap");
    tapEl.hidden = !tap || !window.I18N.de["tap_" + tap.result];
    if (tap && !tapEl.hidden) {
      tapEl.textContent = t("tap_" + tap.result, eur(tap.amount_cents)) +
        (tap.balance_cents !== null && tap.balance_cents !== undefined ? " · " + t("balance", eur(tap.balance_cents)) : "");
      tapEl.dataset.ok = String(tap.result === "checked_in" || tap.result === "checked_out");
    }
    const ses = !connLost && last && last.session;
    const sesEl = document.getElementById("session");
    sesEl.hidden = !ses || !!tap;
    if (ses) sesEl.textContent = t("session", new Date(ses.started_at).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" }), eur(ses.amount_cents));
    document.getElementById("updated").textContent = t("updated", fmtTime(last && last.last_update));

    const a = !connLost && last && last.alert;
    const text = a ? t("alert", fmtTime(a.occurred_at)) : "";
    if (text !== shownAlert) {
      shownAlert = text; // nur bei Änderung neu setzen (Screenreader)
      const box2 = document.getElementById("alert");
      box2.replaceChildren(...(a ? [icon("vib"), Object.assign(document.createElement("p"), { textContent: text })] : []));
    }
  }

  // Neuer Anzeige-Link (anderes Fragment) -> neu laden
  window.addEventListener("hashchange", () => location.reload());
  setInterval(render, 1000);
  applyLang();
  poll();
})();
