// Öffentliche Status-Seite einer Organisation (/status#bst_…): Betriebszustand der Stellplätze und Störungen.
// Keine Belegung, keine Karten, keine Personen – nur, ob die Technik funktioniert.
import { $, h, icon, i18n, token } from "./public-kit.js";

const DICT = {
  de: {
    title: "Betriebsstatus", invalid: "Dieser Status-Link ist ungültig oder deaktiviert.", stalls: "Stellplätze",
    open: "Aktuelle Störungen", history: "Verlauf (30 Tage)", none_open: "Keine aktuellen Störungen.", none_hist: "Keine Störungen in den letzten 30 Tagen.",
    all_ok: "Alle Stellplätze in Betrieb", some: "{n} von {m} Stellplätzen eingeschränkt", no_conn: "Status gerade nicht abrufbar",
    s_ok: "In Betrieb", s_fault: "Gestört", s_maintenance: "Wartung", s_no_data: "Keine aktuellen Daten",
    k_gateway_offline: "Verbindung unterbrochen", k_sensor_fault: "Sensorfehler", k_maintenance: "Wartung",
    since: "seit {t}", dur: "{d}", avail: "Verfügbarkeit {p}",
    simulated: "Enthält Daten aus der Simulation (Beispiel-Stellplatz).",
    about: "Diese Seite zeigt nur, ob die Technik der Stellplätze funktioniert – keine Belegung und keine personenbezogenen Daten. Wartung zählt nicht als Ausfall.",
  },
  nl: {
    title: "Bedrijfsstatus", invalid: "Deze statuslink is ongeldig of uitgeschakeld.", stalls: "Fietsplekken",
    open: "Actuele storingen", history: "Historie (30 dagen)", none_open: "Geen actuele storingen.", none_hist: "Geen storingen in de afgelopen 30 dagen.",
    all_ok: "Alle fietsplekken in bedrijf", some: "{n} van {m} fietsplekken beperkt", no_conn: "Status nu niet op te vragen",
    s_ok: "In bedrijf", s_fault: "Storing", s_maintenance: "Onderhoud", s_no_data: "Geen actuele gegevens",
    k_gateway_offline: "Verbinding onderbroken", k_sensor_fault: "Sensorfout", k_maintenance: "Onderhoud",
    since: "sinds {t}", dur: "{d}", avail: "Beschikbaarheid {p}",
    simulated: "Bevat gegevens uit de simulatie (voorbeeldfietsplek).",
    about: "Deze pagina toont alleen of de techniek van de fietsplekken werkt – geen bezetting en geen persoonsgegevens. Onderhoud telt niet als uitval.",
  },
  en: {
    title: "Service status", invalid: "This status link is invalid or disabled.", stalls: "Stalls",
    open: "Current incidents", history: "History (30 days)", none_open: "No current incidents.", none_hist: "No incidents in the last 30 days.",
    all_ok: "All stalls operational", some: "{n} of {m} stalls affected", no_conn: "Status currently unavailable",
    s_ok: "Operational", s_fault: "Disrupted", s_maintenance: "Maintenance", s_no_data: "No current data",
    k_gateway_offline: "Connection lost", k_sensor_fault: "Sensor fault", k_maintenance: "Maintenance",
    since: "since {t}", dur: "{d}", avail: "Availability {p}",
    simulated: "Contains data from the simulation (example stall).",
    about: "This page only shows whether the stall technology works – no occupancy and no personal data. Maintenance does not count as downtime.",
  },
};
const ICON = { ok: "free", fault: "fault", maintenance: "closed", no_data: "unknown" };
const TOKEN = token("bst_");
const L = i18n(DICT, render);
const { t } = L;
let data = null;
let lastOk = 0;

const when = (iso) => new Date(iso).toLocaleString(L.lang, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
function dur(s) {
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
  return `${Math.floor(s / 86400)} d ${Math.round((s % 86400) / 3600)} h`;
}

async function load() {
  if (!TOKEN) { $("invalid").hidden = false; return; }
  try {
    const r = await fetch("/api/v1/public/status", { cache: "no-store", credentials: "omit", headers: { "X-Status-Token": TOKEN } });
    if (r.status === 404) { $("invalid").hidden = false; data = null; }
    else if (r.ok) { data = await r.json(); lastOk = Date.now(); $("invalid").hidden = true; }
  } catch (_) { /* Anzeige „nicht abrufbar“ */ }
  render();
}

function incident(x) {
  return h("li", { "data-kind": x.kind },
    h("strong", {}, `${x.station_name}: ${t("k_" + x.kind)}`),
    h("span", { class: "muted" }, x.ended_at ? `${when(x.started_at)} · ${dur(x.duration_s)}` : t("since", { t: when(x.started_at) })),
    x.note ? h("span", {}, x.note) : null);
}

function render() {
  L.apply();
  const stale = !data || Date.now() - lastOk > 5 * 60000;
  if (data) { $("org").textContent = data.org; document.title = `${t("title")} · ${data.org}`; }
  const ov = $("overall");
  if (!data || stale) {
    ov.dataset.state = "no_data";
    ov.replaceChildren(icon("unknown", "status-ico"), h("span", {}, t("no_conn")));
  } else {
    const bad = data.stalls.filter((s) => s.state !== "ok").length;
    ov.dataset.state = bad ? "fault" : "ok";
    ov.replaceChildren(icon(bad ? "fault" : "free", "status-ico"), h("span", {}, bad ? t("some", { n: bad, m: data.stalls.length }) : t("all_ok")));
  }
  $("sim").hidden = !(data && data.simulated);
  $("stalls").replaceChildren(...(data ? data.stalls : []).map((s) => h("li", { "data-state": stale ? "no_data" : s.state },
    icon(ICON[stale ? "no_data" : s.state], "status-ico-s"), h("span", { class: "status-name" }, s.name),
    h("span", { class: "status-word" }, t("s_" + (stale ? "no_data" : s.state))),
    s.availability !== null ? h("span", { class: "muted small" }, t("avail", { p: `${(s.availability * 100).toFixed(1)} %` })) : null)));
  $("open").replaceChildren(...(data && data.open.length ? data.open.map(incident) : [h("li", { class: "muted" }, t("none_open"))]));
  $("history").replaceChildren(...(data && data.history.length ? data.history.map(incident) : [h("li", { class: "muted" }, t("none_hist"))]));
}

render();
load();
setInterval(load, 60000);
