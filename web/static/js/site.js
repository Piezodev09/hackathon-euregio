// Großanzeige einer Anlage (öffentlich, /a#<token>): „3 von 10 frei“ und Zustand je Stellplatz.
// Frei zählt nur eine gültige, aktuelle Messung. Ohne Verbindung: nichts als frei zeigen.
import { $, h, icon, i18n, token } from "./public-kit.js";

const DICT = {
  de: {
    of: "von {n} Stellplätzen frei", none_free: "Kein Stellplatz frei", invalid: "Dieser Anzeige-Link ist ungültig oder deaktiviert.",
    connection: "Keine Verbindung zur Plattform – aktueller Zustand unbekannt.", simulated: "SIMULATION",
    available: "FREI", occupied: "BELEGT", reserved: "RESERVIERT", closed: "GESCHLOSSEN", unknown: "UNBEKANNT",
    closed_maint: "AUSSER BETRIEB", min_left: "noch {m} min",
    wait: "Alles voll? In der App auf die Warteliste setzen – Sie werden benachrichtigt, sobald ein Platz frei wird. {n} warten gerade.",
    wait0: "Alles voll? In der App auf die Warteliste setzen – Sie werden benachrichtigt, sobald ein Platz frei wird.",
    legend: "Frei zählt nur ein Platz mit aktueller, gültiger Messung. UNBEKANNT heißt: gerade keine sichere Aussage möglich.",
  },
  nl: {
    of: "van {n} fietsplekken vrij", none_free: "Geen fietsplek vrij", invalid: "Deze weergavelink is ongeldig of uitgeschakeld.",
    connection: "Geen verbinding met het platform – actuele status onbekend.", simulated: "SIMULATIE",
    available: "VRIJ", occupied: "BEZET", reserved: "GERESERVEERD", closed: "GESLOTEN", unknown: "ONBEKEND",
    closed_maint: "BUITEN GEBRUIK", min_left: "nog {m} min",
    wait: "Alles vol? Zet u in de app op de wachtlijst – u krijgt een melding zodra een plek vrijkomt. Nu wachten er {n}.",
    wait0: "Alles vol? Zet u in de app op de wachtlijst – u krijgt een melding zodra een plek vrijkomt.",
    legend: "Alleen een plek met een actuele, geldige meting telt als vrij. ONBEKEND betekent: nu geen betrouwbare uitspraak mogelijk.",
  },
  en: {
    of: "of {n} stalls free", none_free: "No stall free", invalid: "This display link is invalid or disabled.",
    connection: "No connection to the platform – current status unknown.", simulated: "SIMULATION",
    available: "FREE", occupied: "OCCUPIED", reserved: "RESERVED", closed: "CLOSED", unknown: "UNKNOWN",
    closed_maint: "OUT OF SERVICE", min_left: "{m} min left",
    wait: "All full? Join the waiting list in the app – you will be notified as soon as a stall is free. {n} waiting now.",
    wait0: "All full? Join the waiting list in the app – you will be notified as soon as a stall is free.",
    legend: "Only a stall with a current, valid measurement counts as free. UNKNOWN means: no reliable statement right now.",
  },
};
const STATE_OF = { available: "free", occupied: "occupied", reserved: "reserved", closed: "closed", unknown: "unknown" };

const TOKEN = token("bsa_");
const L = i18n(DICT, render);
const { t } = L;
let last = null;
let lastOk = 0;
let pollMs = 2000;

async function poll() {
  if (!TOKEN) { $("invalid").hidden = false; return; }
  try {
    const r = await fetch("/api/v1/public/site/status", { cache: "no-store", credentials: "omit", headers: { "X-Site-Token": TOKEN } });
    if (r.status === 404) { $("invalid").hidden = false; last = null; render(); setTimeout(poll, 30000); return; }
    if (r.ok) {
      $("invalid").hidden = true;
      last = await r.json();
      lastOk = Date.now();
      pollMs = Math.max(2000, (last.poll_interval_s || 2) * 1000);
    }
  } catch (_) { /* sichtbar als „keine Verbindung“ */ }
  render();
  setTimeout(poll, pollMs);
}

function render() {
  L.apply();
  const stale = !last || Date.now() - lastOk > (last.stale_after_s || 30) * 1000;
  $("conn").hidden = !last || !stale;
  if (last) {
    $("name").textContent = last.name;
    $("location").textContent = last.location || "";
    document.title = last.name;
  }
  const free = stale || !last ? null : last.free;
  const state = free === null ? "unknown" : free > 0 ? "free" : "occupied";
  const sum = $("summary");
  if (sum.dataset.state !== state || !$("summary-icon").firstChild) {
    sum.dataset.state = state;
    $("summary-icon").replaceChildren(icon(state, "site-summary__icon"));
  }
  $("free-count").textContent = free === null ? "–" : String(free);
  $("free-label").textContent = last ? (free === 0 ? t("none_free") : t("of", { n: last.total })) : "";
  $("sim").hidden = !(last && last.simulated);
  const list = $("stalls");
  list.replaceChildren(...(last ? last.stalls : []).map((s) => {
    const cat = stale ? "unknown" : s.category;
    const st = STATE_OF[cat];
    const word = cat === "closed" && s.maintenance ? t("closed_maint") : t(cat);
    const mins = cat === "reserved" && s.reservation_remaining_s ? t("min_left", { m: Math.max(1, Math.ceil(s.reservation_remaining_s / 60)) }) : "";
    return h("li", { class: "site-tile", "data-state": st },
      icon(st, "site-tile__icon"), h("span", { class: "site-tile__name" }, s.name),
      h("span", { class: "site-tile__word" }, word), mins ? h("span", { class: "site-tile__sub" }, mins) : null,
      s.simulated ? h("span", { class: "site-tile__sim" }, t("simulated")) : null);
  }));
  const wait = $("wait");
  wait.hidden = !(last && last.waitlist_enabled);
  if (last && last.waitlist_enabled) wait.textContent = last.waiting ? t("wait", { n: last.waiting }) : t("wait0");
}

window.addEventListener("hashchange", () => location.reload());
setInterval(render, 1000);
render();
poll();
