// Start-Tour: erklärt nach der Registrierung Schritt für Schritt alle Bereiche des Portals.
// Öffnet dabei die jeweilige Seite und hebt den Menüpunkt hervor. Jederzeit über das Hilfe-Menü („?“) wiederholbar,
// auch ab dem zuletzt gesehenen Schritt oder passend zur aktuellen Seite.
// Schritte richten sich nach Rolle (Admin-Seiten nur für Admins) und Tarif (fehlende Funktion: Hinweis statt Fehlerseite).
// Barrierefrei: Dialog mit Überschrift, Fokus auf der Tour-Karte, Tastatur (Esc = beenden, ←/→ = zurück/weiter).
import { patch } from "./api.js";
import { t } from "./i18n.js";
import { el, clear } from "./ui.js";
import { state, go, can } from "./state.js";

const RESUME_KEY = "sbb.tourStep";

// [id, Seite, hervorgehobenes Element, { role, feature }]
const ALL_STEPS = [
  ["welcome", "/", null],
  ["overview", "/", '[data-tour="kpis"]'],
  ["demo", "/", '[data-tour="onboarding"]', { role: "admin" }],
  ["stations", "/stations", 'a[href="#/stations"]'],
  ["gateway", "/devices", 'a[href="#/devices"]', { role: "admin" }],
  ["readers", "/readers", 'a[href="#/readers"]', { role: "admin", feature: "nfc" }],
  ["display", "/stations", 'a[href="#/stations"]', { role: "admin" }],
  ["cards", "/cards", 'a[href="#/cards"]', { feature: "nfc" }],
  ["fees", "/parking-billing", 'a[href="#/parking-billing"]', { feature: "parking_billing" }],
  ["reservations", "/reservations", 'a[href="#/reservations"]', { feature: "reservations" }],
  ["hours", "/stations", 'a[href="#/stations"]', { role: "admin" }],
  ["alerts", "/events", 'a[href="#/events"]'],
  ["stats", "/stats", 'a[href="#/stats"]'],
  ["notifications", "/security", 'a[href="#/security"]'],
  ["reports", "/reports", 'a[href="#/reports"]', { feature: "reports" }],
  ["integrations", "/integrations", 'a[href="#/integrations"]', { role: "admin", feature: "integrations" }],
  ["team", "/team", 'a[href="#/team"]'],
  ["billing", "/billing", 'a[href="#/billing"]'],
  ["help", null, '[data-tour="help"]'],
  ["done", "/", null],
];

// Schritte für die angemeldete Person: ohne Rolle -> weglassen; ohne Tarif-Funktion -> Hinweis, Seite bleibt
function steps() {
  const plan = state.me?.tenant?.plan || {};
  return ALL_STEPS.filter(([, , , req]) => !req?.role || can(req.role))
    .map(([id, route, selector, req]) => {
      const locked = !!(req?.feature && !plan[req.feature]);
      return { id, route: locked ? null : route, selector: locked ? null : selector, locked };
    });
}
let STEPS = [];

function remember(i) { try { localStorage.setItem(RESUME_KEY, String(i)); } catch (_) { /* optional */ } }
function forget() { try { localStorage.removeItem(RESUME_KEY); } catch (_) { /* optional */ } }

/** Zuletzt gesehener Schritt (Index), wenn die Tour mittendrin beendet wurde, sonst -1. */
export function resumeStep() {
  try { const n = parseInt(localStorage.getItem(RESUME_KEY), 10); return Number.isFinite(n) && n > 0 ? n : -1; } catch (_) { return -1; }
}

/** Index des Schritts, der die gegebene Seite erklärt (oder -1). */
export function stepForPath(path) {
  const list = steps();
  if (path === "/") return list.findIndex((x) => x.id === "overview");
  return list.findIndex((x) => x.route && x.route !== "/" && path.startsWith(x.route));
}

export function stepCount() { return steps().length; }

let current = -1;
let card = null;
let backdrop = null;
let highlighted = null;

function unhighlight() {
  if (highlighted) highlighted.classList.remove("tour-target");
  highlighted = null;
}

function place(target) {
  card.classList.toggle("tour-card--center", !target);
  card.style.removeProperty("top");
  card.style.removeProperty("left");
  if (!target || window.innerWidth < 900) return;
  const r = target.getBoundingClientRect();
  if (r.width > window.innerWidth / 2) return; // große Bereiche: Karte unten rechts, Bereich bleibt sichtbar
  const w = card.offsetWidth;
  const h = card.offsetHeight;
  let left = r.right + 16;
  if (left + w > window.innerWidth - 16) left = Math.max(16, r.left - w - 16);
  const top = Math.min(Math.max(16, r.top - 8), window.innerHeight - h - 16);
  card.style.left = `${left}px`;
  card.style.top = `${top}px`;
}

async function show(i) {
  current = i;
  const { id, route, selector, locked } = STEPS[i];
  remember(i);
  if (route && ("#" + route) !== location.hash) {
    go(route);
    await new Promise((r) => setTimeout(r, 450));
  }
  unhighlight();
  const target = selector ? document.querySelector(selector) : null;
  if (target && target.offsetParent !== null) {
    highlighted = target;
    target.classList.add("tour-target");
    target.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  const last = i === STEPS.length - 1;
  clear(card,
    el("p", { class: "tour-step small" }, t("tour.step", { n: i + 1, m: STEPS.length })),
    el("h2", { id: "tour-h", tabindex: "-1" }, t(`tour.${id}_t`)),
    el("p", {}, t(`tour.${id}`)),
    locked ? el("p", { class: "alert-box info small" }, t("tour.locked")) : null,
    el("div", { class: "tour-dots", "aria-hidden": "true" }, STEPS.map((_, k) => el("span", { class: k === i ? "on" : k < i ? "past" : null }))),
    el("div", { class: "btn-row tour-actions" },
      i > 0 ? el("button", { class: "btn", type: "button", onclick: () => show(i - 1) }, t("tour.back")) : null,
      el("button", { class: "btn primary", type: "button", onclick: () => (last ? end(true, true) : show(i + 1)) }, last ? t("tour.finish") : t("tour.next")),
      !last ? el("button", { class: "btn link", type: "button", onclick: () => end(true) }, t("tour.skip")) : null));
  backdrop.hidden = !!highlighted;
  place(highlighted);
  card.querySelector("h2").focus({ preventScroll: true });
}

function onKey(e) {
  if (current < 0) return;
  if (e.key === "Escape") end(true);
  else if (e.key === "ArrowRight" && current < STEPS.length - 1) show(current + 1);
  else if (e.key === "ArrowLeft" && current > 0) show(current - 1);
}

async function end(markDone, finished = false) {
  if (finished) forget();
  unhighlight();
  current = -1;
  card?.remove();
  backdrop?.remove();
  card = null;
  backdrop = null;
  document.removeEventListener("keydown", onKey);
  window.removeEventListener("resize", onResize);
  if (markDone && state.me && !state.me.user.tour_done) {
    state.me.user.tour_done = true;
    try { await patch("/api/v1/auth/me", { tour_done: true }); } catch (_) { /* nicht kritisch */ }
  }
}

function onResize() { if (card) place(highlighted); }

export function startTour(from = 0) {
  if (!state.me?.tenant) return;
  if (card) end(false);
  STEPS = steps();
  from = Math.max(0, Math.min(from, STEPS.length - 1));
  card = el("div", { class: "tour-card", role: "dialog", "aria-modal": "false", "aria-labelledby": "tour-h" });
  backdrop = el("div", { class: "tour-backdrop", hidden: true, onclick: () => card?.querySelector(".btn.primary")?.focus() });
  document.body.append(backdrop, card);
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", onResize);
  show(from);
}

export function tourRunning() { return current >= 0; }
