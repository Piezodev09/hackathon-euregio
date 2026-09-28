// DOM-Helfer ohne innerHTML (XSS-sicher: alle Inhalte als Text/Knoten), Toasts, Dialoge, Formatierung.
import { getLang, setLang, t, STRINGS } from "./i18n.js";

export function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
    else if (k === "checked") e.checked = !!v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
}

export function clear(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

let toastBox;
export function toast(message, kind = "ok") {
  if (!toastBox) {
    toastBox = el("div", { class: "toasts", role: "status", "aria-live": "polite" });
    document.body.append(toastBox);
  }
  const n = el("div", { class: `toast ${kind}` }, message);
  toastBox.append(n);
  setTimeout(() => n.remove(), kind === "error" ? 7000 : 3500);
}

export function confirmDialog(message, { danger = false, confirmLabel } = {}) {
  return new Promise((resolve) => {
    const d = el("dialog", { "aria-modal": "true" },
      el("p", {}, message),
      el("div", { class: "btn-row" },
        el("button", { class: `btn ${danger ? "danger" : "primary"}`, value: "ok", onclick: () => d.close("ok") }, confirmLabel || t("c.confirm")),
        el("button", { class: "btn", value: "cancel", onclick: () => d.close("cancel") }, t("c.cancel"))));
    d.addEventListener("close", () => { resolve(d.returnValue === "ok"); d.remove(); });
    document.body.append(d);
    d.showModal();
  });
}

export function fmtDateTime(iso) {
  if (!iso) return t("c.never");
  return new Date(iso).toLocaleString(getLang(), { dateStyle: "short", timeStyle: "short" });
}
export function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleTimeString(getLang(), { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–";
}
export function fmtCents(c) {
  if (c === null || c === undefined) return "–";
  return new Intl.NumberFormat(getLang(), { style: "currency", currency: "EUR" }).format(c / 100);
}
export function fmtDuration(s) {
  if (s === null || s === undefined) return "–";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h} h ${m % 60} min` : `${Math.floor(h / 24)} d ${h % 24} h`;
}
export function fmtMoney(n) {
  return new Intl.NumberFormat(getLang(), { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(n);
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast(t("c.copied")); } catch (_) { /* Zwischenablage evtl. gesperrt */ }
}

export function langSwitcher(onChange) {
  const nav = el("div", { class: "lang", role: "group", "aria-label": "Sprache / Taal / Language" });
  const render = () => clear(nav, Object.keys(STRINGS).map((l) =>
    el("button", { type: "button", lang: l, "aria-pressed": String(l === getLang()),
      onclick: () => { setLang(l); render(); onChange && onChange(l); } }, l.toUpperCase())));
  render();
  return nav;
}

// Grobe, clientseitige Stärkeanzeige (die verbindliche Prüfung macht der Server).
export function passwordMeter(input, minLength) {
  const bar = el("span");
  bar.style.width = "0%";
  const label = el("span", { class: "small muted" });
  const wrap = el("div", { class: "hint" }, el("div", { class: "meter", "aria-hidden": "true" }, bar), label);
  const update = () => {
    const v = input.value;
    let score = 0;
    if (v.length >= minLength) score++;
    if (v.length >= minLength + 4) score++;
    if (/[a-z]/.test(v) && /[A-Z]/.test(v)) score++;
    if (/\d/.test(v) && /[^A-Za-z0-9]/.test(v)) score++;
    if (new Set(v).size < 5) score = 0;
    const pct = [5, 30, 55, 80, 100][score];
    bar.style.width = pct + "%";
    label.textContent = v ? `${t("auth.pw_strength")}: ${t(score < 2 ? "auth.pw_weak" : score < 4 ? "auth.pw_ok" : "auth.pw_strong")}` : "";
  };
  input.addEventListener("input", update);
  return wrap;
}

export function field(labelText, input, hint) {
  if (!input.id) input.id = "f_" + Math.random().toString(36).slice(2, 9);
  return el("div", { class: "field" }, el("label", { for: input.id }, labelText), input, hint ? el("div", { class: "hint" }, hint) : null);
}

// ---------------------------------------------------------------------- Icons (Design-System)
// Feste SVG-Vorlagen (keine Nutzerdaten) -> per DOMParser in Knoten umgewandelt.
const NS = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"';
const ICONS = {
  free: `<svg ${NS}><circle cx="24" cy="24" r="20" fill="none" stroke="currentColor" stroke-width="4"/><path d="M14 25l7 7 13-15" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  occupied: `<svg ${NS}><circle cx="12" cy="32" r="8" fill="none" stroke="currentColor" stroke-width="3.5"/><circle cx="36" cy="32" r="8" fill="none" stroke="currentColor" stroke-width="3.5"/><path d="M12 32l8-14h11l5 14M20 18l7 14h-15M31 18l-2-6h5M17 13h6" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  unknown: `<svg ${NS}><circle cx="24" cy="24" r="20" fill="none" stroke="currentColor" stroke-width="4" stroke-dasharray="7 4"/><path d="M18 19a6 6 0 1 1 8.4 5.5c-1.6.8-2.4 2-2.4 3.5v1.5" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><circle cx="24" cy="35.5" r="2.8" fill="currentColor"/></svg>`,
  warn: `<svg ${NS}><path d="M24 6 44 41H4Z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/><path d="M24 19v10" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><circle cx="24" cy="35" r="2.6" fill="currentColor"/></svg>`,
  ok: `<svg ${NS}><circle cx="24" cy="24" r="19" fill="none" stroke="currentColor" stroke-width="4"/><path d="M15 25l6 6 12-13" fill="none" stroke="currentColor" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  fault: `<svg ${NS}><circle cx="24" cy="24" r="19" fill="none" stroke="currentColor" stroke-width="4"/><path d="M17 17l14 14M31 17 17 31" stroke="currentColor" stroke-width="4.5" stroke-linecap="round"/></svg>`,
  nodata: `<svg ${NS}><circle cx="24" cy="24" r="19" fill="none" stroke="currentColor" stroke-width="4" stroke-dasharray="6 4"/><path d="M16 24h16" stroke="currentColor" stroke-width="4.5" stroke-linecap="round"/></svg>`,
  info: `<svg ${NS}><circle cx="24" cy="24" r="19" fill="none" stroke="currentColor" stroke-width="4"/><path d="M24 22v13" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><circle cx="24" cy="15" r="2.6" fill="currentColor"/></svg>`,
  vib: `<svg ${NS}><path d="M4 24h7l4-10 6 20 6-24 6 20 4-6h7" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  card: `<svg ${NS}><rect x="5" y="11" width="38" height="26" rx="4" fill="none" stroke="currentColor" stroke-width="4"/><path d="M31 20a6 6 0 0 1 0 8M35 17a10 10 0 0 1 0 14" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><path d="M11 30h12" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>`,
  camera: `<svg ${NS}><path d="M6 16h8l4-6h12l4 6h8v24H6Z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/><circle cx="24" cy="27" r="7" fill="none" stroke="currentColor" stroke-width="4"/></svg>`,
  wrench: `<svg ${NS}><path d="M30 6a10 10 0 0 0-9 14L7 34a4 4 0 0 0 6 6l14-14a10 10 0 0 0 14-9l-6 6-6-2-2-6Z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/></svg>`,
  reserved: `<svg ${NS}><circle cx="24" cy="27" r="16" fill="none" stroke="currentColor" stroke-width="4"/><path d="M24 18v9l6 5" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/><path d="M18 5h12M24 5v6" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>`,
  lock: `<svg ${NS}><rect x="10" y="21" width="28" height="21" rx="3" fill="none" stroke="currentColor" stroke-width="4"/><path d="M16 21v-5a8 8 0 0 1 16 0v5" fill="none" stroke="currentColor" stroke-width="4"/><circle cx="24" cy="31" r="3" fill="currentColor"/></svg>`,
  bell: `<svg ${NS}><path d="M12 34V22a12 12 0 0 1 24 0v12l4 4H8Z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/><path d="M20 42a4 4 0 0 0 8 0" fill="none" stroke="currentColor" stroke-width="4"/></svg>`,
  report: `<svg ${NS}><path d="M10 6h20l8 8v28H10Z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/><path d="M17 34v-6M24 34V22M31 34v-9" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>`,
  plug: `<svg ${NS}><path d="M18 6v10M30 6v10M12 16h24v8a12 12 0 0 1-24 0Z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/><path d="M24 36v8" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>`,
  wallet: `<svg ${NS}><rect x="5" y="12" width="38" height="28" rx="4" fill="none" stroke="currentColor" stroke-width="4"/><path d="M5 18h33M31 29h6" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>`,
  help: `<svg ${NS}><circle cx="24" cy="24" r="19" fill="none" stroke="currentColor" stroke-width="4"/><path d="M18.5 19a5.5 5.5 0 1 1 7.7 5c-1.4.7-2.2 1.8-2.2 3.2v1.3" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><circle cx="24" cy="35" r="2.6" fill="currentColor"/></svg>`,
  play: `<svg ${NS}><circle cx="24" cy="24" r="19" fill="none" stroke="currentColor" stroke-width="4"/><path d="M20 16l12 8-12 8Z" fill="currentColor"/></svg>`,
  logo: `<svg ${NS}><path d="M6 42V12h36v30" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/><rect x="14" y="14" width="20" height="8" rx="1" fill="currentColor"/><circle cx="16" cy="35" r="5" fill="none" stroke="currentColor" stroke-width="3"/><circle cx="32" cy="35" r="5" fill="none" stroke="currentColor" stroke-width="3"/><path d="M16 35l5-8h7l4 8" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/></svg>`,
};
const parser = new DOMParser();
export function icon(name, cls = "sbb-icon") {
  const svg = document.importNode(parser.parseFromString(ICONS[name] || ICONS.unknown, "image/svg+xml").documentElement, true);
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  return svg;
}

// ---------------------------------------------------------------------- Stellplatz-Zustand
// Grundregel: FREI/BELEGT nur aus gültiger, aktueller Messung. Bricht die Verbindung zur API ab,
// zeigt die Oberfläche STATUS UNBEKANNT – nie den zuletzt bekannten Zustand.
export function effectiveState(status, connLost) {
  if (!status || connLost) return { state: "unknown", reason: "connection" };
  return { state: status.state, reason: status.state === "unknown" ? status.unknown_reason || "no_data" : null };
}

// Restzeit einer Reservierung (lokal weitergezählt zwischen zwei Abfragen)
export function remainingMin(reservation) {
  if (!reservation) return 0;
  return Math.max(1, Math.ceil((new Date(reservation.until) - Date.now()) / 60000));
}

// "Geschlossen"-Hinweis: Öffnungszeiten oder Sperrzeit (Ferien …)
export function closedText(closed) {
  if (!closed) return "";
  const when = closed.opens_at ? new Date(closed.opens_at).toLocaleString(getLang(), { weekday: "short", hour: "2-digit", minute: "2-digit" }) : null;
  const base = closed.reason === "closure" ? t("st.closed_closure", { note: closed.note || t("st.closure_default") }) : t("st.closed_hours");
  return when ? `${base} ${t("st.opens_at", { t: when })}` : base;
}

export function reasonText(reason, staleAfterS = 30) {
  return t("st.r_" + reason, { s: staleAfterS });
}

// Große Status-Karte (Wort + Symbol + Farbe + bei UNBEKANNT Schraffur).
export function stallStatus(status, { connLost = false, simulated = false } = {}) {
  const { state, reason } = effectiveState(status, connLost);
  const sub = state === "unknown" ? reasonText(reason, status?.stale_after_s)
    : state === "reserved" ? t("st.sub_reserved", { m: remainingMin(status.reservation) }) : t("st.sub_" + state);
  return el("div", { class: "sbb-status", "data-state": state },
    icon(state, "sbb-status__icon"),
    el("div", {}, el("p", { class: "sbb-status__word" }, t("st." + state)), el("p", { class: "sbb-status__sub" }, sub)),
    simulated ? el("span", { class: "sbb-status__corner sbb-tag sbb-tag--demo" }, t("st.sim")) : null);
}

// Kompaktes Badge für Listen.
export function stallBadge(state) {
  return el("span", { class: "sbb-badge", "data-state": state }, icon(state), t("st." + state));
}

export function fmtAge(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${Math.floor(s / 3600)} h`;
}
