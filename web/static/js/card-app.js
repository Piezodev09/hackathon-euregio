// Karten-App für Radfahrende (/k, als App installierbar).
// Zugang über den persönlichen Karten-Link der Betreuung (/k#bck_…). Der Link wird nur auf diesem Gerät gespeichert.
// Grundsatz wie überall: ohne aktuelle Daten nie „frei“ – dann „keine aktuellen Daten“.
import { $, h, icon, i18n, eur, token } from "./public-kit.js";

const DICT = {
  de: {
    title: "Meine Karte", t_home: "Start", t_stalls: "Plätze", t_history: "Verlauf", t_settings: "Einstellungen",
    connection: "Keine Verbindung – angezeigte Zustände sind nicht aktuell.",
    no_link: "Diese App braucht Ihren persönlichen Karten-Link.",
    no_link_text: "Sie bekommen ihn bei der Betreuung (Sekretariat, Hausmeister) als QR-Code zu Ihrer Karte. Einfach scannen – die App merkt sich den Link auf diesem Gerät.",
    invalid: "Dieser Karten-Link ist ungültig, wurde erneuert oder die Karte ist gesperrt. Bitte bei der Betreuung einen neuen Link holen.",
    balance: "Guthaben", topup_hint: "Aufladen bei der Betreuung.", statement: "Gebühren werden monatlich mit der Organisation abgerechnet.",
    parked: "Eingecheckt an {s}", parked_since: "seit {t} · bisher {p}", not_parked: "Gerade nicht eingecheckt.",
    res_title: "Reserviert: {s}", res_left: "noch {m} Minuten – Karte am Leser anhalten, um einzuchecken.", cancel: "Freigeben",
    wl_title: "Warteliste {s}", wl_pos: "Sie sind auf Platz {n}. Wir melden uns, sobald ein Stellplatz frei wird.",
    wl_offer: "Platz frei! {s} ist für Sie reserviert.", leave: "Warteliste verlassen",
    free_of: "{f} von {n} frei", loose: "Weitere Stellplätze",
    available: "FREI", occupied: "BELEGT", reserved: "RESERVIERT", closed: "GESCHLOSSEN", unknown: "UNBEKANNT", simulated: "SIMULATION",
    reserve: "15 min reservieren", reserve_off: "Reservieren ist bei Ihrer Organisation nicht freigeschaltet.",
    join: "Auf die Warteliste", wl_off: "Warteliste an dieser Anlage nicht aktiv.",
    sessions: "Parkvorgänge", txns: "Buchungen", none: "Noch nichts.", running: "läuft",
    k_topup: "Aufladung", k_fee: "Parkgebühr", k_correction: "Korrektur",
    push_title: "Benachrichtigungen", push_on: "Benachrichtigungen aktivieren", push_off: "Benachrichtigungen ausschalten",
    push_test: "Test senden", push_active: "Aktiv auf diesem Gerät.", push_inactive: "Aus. Für die Warteliste empfohlen.",
    push_denied: "Im Browser blockiert – bitte in den Einstellungen des Browsers erlauben.",
    push_unsupported: "Dieser Browser unterstützt keine Push-Nachrichten. Auf dem iPhone: erst „Zum Home-Bildschirm“, dann die App von dort öffnen (ab iOS 16.4).",
    push_sent: "Testnachricht gesendet.", push_err: "Das hat nicht geklappt.",
    install: "Als App installieren", install_hint: "Tipp: Über das Teilen-Menü „Zum Home-Bildschirm“ hinzufügen.",
    logout: "Auf diesem Gerät abmelden", logout_confirm: "Karten-Link von diesem Gerät entfernen?",
    privacy: "Gespeichert wird nur Ihr Karten-Link auf diesem Gerät. Keine Namen, kein Standort. Den Link kann die Betreuung jederzeit sperren.",
    err_not_free: "Der Platz ist gerade nicht sicher frei.", err_generic: "Das hat nicht geklappt. Bitte erneut versuchen.",
    err_already: "Sie haben bereits eine Reservierung.", err_parked: "Sie sind bereits eingecheckt.",
    err_stall_available: "Es ist gerade ein Platz frei – einfach hinfahren und einchecken.",
    err_already_waiting: "Sie stehen bereits auf einer Warteliste.", wl_joined: "Sie stehen auf der Warteliste (Platz {n}). Sie bekommen eine Nachricht, sobald ein Platz frei wird.",
    updated: "Stand {t}",
  },
  nl: {
    title: "Mijn kaart", t_home: "Start", t_stalls: "Plekken", t_history: "Historie", t_settings: "Instellingen",
    connection: "Geen verbinding – de getoonde statussen zijn niet actueel.",
    no_link: "Deze app heeft uw persoonlijke kaartlink nodig.",
    no_link_text: "U krijgt die bij de begeleiding (secretariaat, conciërge) als QR-code bij uw kaart. Scannen – de app onthoudt de link op dit apparaat.",
    invalid: "Deze kaartlink is ongeldig, vernieuwd of de kaart is geblokkeerd. Haal een nieuwe link bij de begeleiding.",
    balance: "Tegoed", topup_hint: "Opwaarderen bij de begeleiding.", statement: "Kosten worden maandelijks met de organisatie verrekend.",
    parked: "Ingecheckt bij {s}", parked_since: "sinds {t} · tot nu toe {p}", not_parked: "Nu niet ingecheckt.",
    res_title: "Gereserveerd: {s}", res_left: "nog {m} minuten – houd uw kaart bij de lezer om in te checken.", cancel: "Vrijgeven",
    wl_title: "Wachtlijst {s}", wl_pos: "U staat op plaats {n}. We melden ons zodra een fietsplek vrijkomt.",
    wl_offer: "Plek vrij! {s} is voor u gereserveerd.", leave: "Wachtlijst verlaten",
    free_of: "{f} van {n} vrij", loose: "Overige fietsplekken",
    available: "VRIJ", occupied: "BEZET", reserved: "GERESERVEERD", closed: "GESLOTEN", unknown: "ONBEKEND", simulated: "SIMULATIE",
    reserve: "15 min reserveren", reserve_off: "Reserveren is bij uw organisatie niet ingeschakeld.",
    join: "Op de wachtlijst", wl_off: "Wachtlijst bij deze locatie niet actief.",
    sessions: "Parkeersessies", txns: "Boekingen", none: "Nog niets.", running: "loopt",
    k_topup: "Opwaardering", k_fee: "Parkeerkosten", k_correction: "Correctie",
    push_title: "Meldingen", push_on: "Meldingen inschakelen", push_off: "Meldingen uitschakelen",
    push_test: "Test versturen", push_active: "Actief op dit apparaat.", push_inactive: "Uit. Aanbevolen voor de wachtlijst.",
    push_denied: "Geblokkeerd in de browser – sta ze toe in de browserinstellingen.",
    push_unsupported: "Deze browser ondersteunt geen pushmeldingen. Op de iPhone: eerst „Zet op beginscherm”, dan de app daar openen (vanaf iOS 16.4).",
    push_sent: "Testmelding verstuurd.", push_err: "Dat is niet gelukt.",
    install: "Als app installeren", install_hint: "Tip: via het deelmenu „Zet op beginscherm” toevoegen.",
    logout: "Op dit apparaat afmelden", logout_confirm: "Kaartlink van dit apparaat verwijderen?",
    privacy: "Alleen uw kaartlink wordt op dit apparaat bewaard. Geen namen, geen locatie. De begeleiding kan de link altijd blokkeren.",
    err_not_free: "De plek is nu niet zeker vrij.", err_generic: "Dat is niet gelukt. Probeer het opnieuw.",
    err_already: "U heeft al een reservering.", err_parked: "U bent al ingecheckt.",
    err_stall_available: "Er is nu een plek vrij – ga er gewoon heen en check in.",
    err_already_waiting: "U staat al op een wachtlijst.", wl_joined: "U staat op de wachtlijst (plaats {n}). U krijgt een melding zodra een plek vrijkomt.",
    updated: "Stand {t}",
  },
  en: {
    title: "My card", t_home: "Home", t_stalls: "Stalls", t_history: "History", t_settings: "Settings",
    connection: "No connection – the states shown are not current.",
    no_link: "This app needs your personal card link.",
    no_link_text: "You get it from staff (office, caretaker) as a QR code with your card. Scan it – the app remembers the link on this device.",
    invalid: "This card link is invalid, was renewed or the card is blocked. Please get a new link from staff.",
    balance: "Balance", topup_hint: "Top up with staff.", statement: "Fees are billed monthly by your organisation.",
    parked: "Checked in at {s}", parked_since: "since {t} · so far {p}", not_parked: "Not checked in right now.",
    res_title: "Reserved: {s}", res_left: "{m} minutes left – hold your card to the reader to check in.", cancel: "Release",
    wl_title: "Waiting list {s}", wl_pos: "You are number {n}. We will notify you as soon as a stall is free.",
    wl_offer: "Stall free! {s} is reserved for you.", leave: "Leave waiting list",
    free_of: "{f} of {n} free", loose: "Other stalls",
    available: "FREE", occupied: "OCCUPIED", reserved: "RESERVED", closed: "CLOSED", unknown: "UNKNOWN", simulated: "SIMULATION",
    reserve: "Reserve 15 min", reserve_off: "Reserving is not enabled by your organisation.",
    join: "Join waiting list", wl_off: "Waiting list not active at this site.",
    sessions: "Parking sessions", txns: "Transactions", none: "Nothing yet.", running: "running",
    k_topup: "Top-up", k_fee: "Parking fee", k_correction: "Correction",
    push_title: "Notifications", push_on: "Turn on notifications", push_off: "Turn off notifications",
    push_test: "Send test", push_active: "Active on this device.", push_inactive: "Off. Recommended for the waiting list.",
    push_denied: "Blocked in the browser – please allow them in the browser settings.",
    push_unsupported: "This browser does not support push notifications. On iPhone: first “Add to Home Screen”, then open the app from there (iOS 16.4+).",
    push_sent: "Test notification sent.", push_err: "That did not work.",
    install: "Install as app", install_hint: "Tip: use the share menu → “Add to Home Screen”.",
    logout: "Sign out on this device", logout_confirm: "Remove the card link from this device?",
    privacy: "Only your card link is stored on this device. No names, no location. Staff can block the link at any time.",
    err_not_free: "The stall is not reliably free right now.", err_generic: "That did not work. Please try again.",
    err_already: "You already have a reservation.", err_parked: "You are already checked in.",
    err_stall_available: "A stall is free right now – just go there and check in.",
    err_already_waiting: "You are already on a waiting list.", wl_joined: "You are on the waiting list (number {n}). You will be notified as soon as a stall is free.",
    updated: "Updated {t}",
  },
};
const STATE_OF = { available: "free", occupied: "occupied", reserved: "reserved", closed: "closed", unknown: "unknown" };
const STORE = "sbb.cardLink";

let TOKEN = token("bck_", STORE);
const L = i18n(DICT, () => render());
const { t } = L;
let data = null;
let lastOk = 0;
let tab = "home";
let invalid = false;
let installEvt = null;
let flash = null;  // kurze Meldung nach einer Aktion
let lastOfferId = null;

const time = (iso) => new Date(iso).toLocaleTimeString(L.lang, { hour: "2-digit", minute: "2-digit" });
const dateTime = (iso) => new Date(iso).toLocaleString(L.lang, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const money = (c) => eur(c, L.lang);

async function api(method, path, body) {
  const r = await fetch(path, { method, cache: "no-store", credentials: "omit",
    headers: { "X-Card-Token": TOKEN, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined });
  const json = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error("api"), { status: r.status, code: typeof json.detail === "string" ? json.detail : json.detail?.code });
  return json;
}

async function load() {
  if (!TOKEN) { render(); return; }
  try {
    data = await api("GET", "/api/v1/public/card");
    invalid = false;
    lastOk = Date.now();
    notifyOffer();
  } catch (e) {
    if (e.status === 404) { invalid = true; data = null; }
  }
  render();
}

// Seite offen und ein Platz wird angeboten: auch ohne Push deutlich melden
function notifyOffer() {
  const w = data && data.waitlist;
  const key = w && w.status === "offered" && data.reservation ? data.reservation.id : null;
  if (key && key !== lastOfferId) {
    lastOfferId = key;
    if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
  }
}

const stale = () => !data || Date.now() - lastOk > 30000;

function msg(e) {
  const map = { not_free: "err_not_free", already_reserved_by_card: "err_already", already_parked: "err_parked",
    stall_available: "err_stall_available", already_waiting: "err_already_waiting", self_reservation_disabled: "reserve_off" };
  return t(map[e.code] || "err_generic");
}

async function act(fn, okText) {
  try { flash = null; await fn(); if (okText) flash = { kind: "ok", text: okText }; } catch (e) { flash = { kind: "err", text: msg(e) }; }
  await load();
}

// ---------------------------------------------------------------------- Ansichten
function home() {
  const d = data;
  const out = [];
  const c = d.card;
  out.push(h("section", { class: "app-card" }, h("p", { class: "muted small" }, d.org.name),
    h("h1", {}, c.label || "–"),
    c.prepaid ? [h("p", { class: "app-balance" }, h("span", { class: "muted" }, t("balance"), " "), money(c.balance_cents)),
      h("p", { class: "small muted" }, t("topup_hint"))] : h("p", { class: "small muted" }, t("statement"))));
  const w = d.waitlist;
  if (w && w.status === "offered" && d.reservation) {
    out.push(h("section", { class: "app-card app-card--offer", role: "status" }, icon("bell", "app-ico"),
      h("h2", {}, t("wl_offer", { s: d.reservation.station_name })),
      h("p", {}, t("res_left", { m: Math.max(1, Math.ceil(d.reservation.remaining_s / 60)) })),
      h("button", { class: "btn", type: "button", onclick: () => act(() => api("DELETE", "/api/v1/public/card/waitlist")) }, t("leave"))));
  } else if (d.reservation) {
    out.push(h("section", { class: "app-card app-card--reserved" }, icon("reserved", "app-ico"),
      h("h2", {}, t("res_title", { s: d.reservation.station_name })),
      h("p", {}, t("res_left", { m: Math.max(1, Math.ceil(d.reservation.remaining_s / 60)) })),
      h("button", { class: "btn", type: "button", onclick: () => act(() => api("DELETE", `/api/v1/public/card/reservations/${d.reservation.id}`)) }, t("cancel"))));
  }
  if (w && w.status === "waiting") {
    out.push(h("section", { class: "app-card" }, icon("bell", "app-ico"), h("h2", {}, t("wl_title", { s: w.site_name })),
      h("p", {}, t("wl_pos", { n: w.position })),
      h("button", { class: "btn", type: "button", onclick: () => act(() => api("DELETE", "/api/v1/public/card/waitlist")) }, t("leave"))));
  }
  out.push(h("section", { class: "app-card" }, d.session
    ? [h("h2", {}, t("parked", { s: d.session.station_name })), h("p", {}, t("parked_since", { t: time(d.session.started_at), p: money(d.session.amount_cents) })),
      d.session.simulated ? h("span", { class: "sbb-tag sbb-tag--demo" }, t("simulated")) : null]
    : h("p", { class: "muted" }, t("not_parked"))));
  return out;
}

function stallTile(s, canReserve) {
  const cat = stale() ? "unknown" : s.category;
  return h("li", { class: "app-tile", "data-state": STATE_OF[cat] }, icon(STATE_OF[cat], "app-tile__ico"),
    h("span", { class: "app-tile__name" }, s.name), h("span", { class: "app-tile__word" }, t(cat)),
    s.simulated ? h("span", { class: "app-tile__sim" }, t("simulated")) : null,
    canReserve && cat === "available" ? h("button", { class: "btn small", type: "button",
      onclick: () => act(async () => {
        await api("POST", "/api/v1/public/card/reservations", { station_id: s.station_id, minutes: 15 });
        tab = "home";
      }) }, t("reserve")) : null);
}

function stalls() {
  const d = data;
  const busy = !!(d.reservation || d.session);
  const canReserve = d.features.reserve && !busy;
  return [
    !d.features.reserve ? h("p", { class: "small muted" }, t("reserve_off")) : null,
    ...d.sites.map((s) => h("section", { class: "app-card" },
      h("div", { class: "app-site-head" }, h("h2", {}, s.name || t("loose")),
        h("span", { class: "app-site-count" }, stale() ? "–" : t("free_of", { f: s.free, n: s.total }))),
      h("ul", { class: "app-tiles" }, s.stalls.map((x) => stallTile(x, canReserve))),
      s.id && !stale() && s.free === 0 && d.features.waitlist && !d.waitlist && !busy
        ? (s.waitlist_enabled
          ? h("button", { class: "btn primary", type: "button", onclick: () => act(async () => {
            const r = await api("POST", "/api/v1/public/card/waitlist", { site_id: s.id });
            tab = "home";
            flash = { kind: "ok", text: t("wl_joined", { n: r.position }) };
          }) },
            icon("bell", "btn-ico"), t("join"))
          : h("p", { class: "small muted" }, t("wl_off"))) : null)),
  ];
}

function history() {
  const d = data;
  return [
    h("section", { class: "app-card" }, h("h2", {}, t("sessions")),
      d.sessions.length ? h("ul", { class: "app-list" }, d.sessions.map((s) => h("li", {},
        h("span", {}, h("strong", {}, s.station_name || "–"), h("br"), h("span", { class: "small muted" }, dateTime(s.started_at))),
        h("span", { class: "app-amount" }, s.running ? t("running") : money(s.amount_cents))))) : h("p", { class: "muted" }, t("none"))),
    d.card.prepaid ? h("section", { class: "app-card" }, h("h2", {}, t("txns")),
      d.transactions.length ? h("ul", { class: "app-list" }, d.transactions.map((x) => h("li", {},
        h("span", {}, h("strong", {}, t("k_" + x.kind)), h("br"), h("span", { class: "small muted" }, dateTime(x.at))),
        h("span", { class: `app-amount ${x.amount_cents < 0 ? "neg" : "pos"}` }, money(x.amount_cents))))) : h("p", { class: "muted" }, t("none"))) : null,
  ];
}

// ---------------------------------------------------------------------- Push
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

async function currentSub() {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.getRegistration("/");
  return reg ? reg.pushManager.getSubscription() : null;
}

function b64ToBytes(b64) {
  const s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64.length + 3) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

async function pushOn() {
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw Object.assign(new Error("denied"), { code: "denied" });
  const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  await navigator.serviceWorker.ready;
  const { key } = await (await fetch("/api/v1/public/push-key", { credentials: "omit" })).json();
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
  await api("POST", "/api/v1/public/card/push", sub.toJSON());
}

async function pushOff() {
  const sub = await currentSub();
  if (sub) {
    await api("POST", "/api/v1/public/card/push/off", { endpoint: sub.endpoint }).catch(() => {});
    await sub.unsubscribe();
  }
}

let pushState = null;  // null | "on" | "off" | "denied" | "unsupported"
async function refreshPush() {
  if (!pushSupported()) pushState = "unsupported";
  else if (Notification.permission === "denied") pushState = "denied";
  else pushState = (await currentSub()) ? "on" : "off";
}

function settings() {
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  const push = h("section", { class: "app-card" }, h("h2", {}, icon("bell", "app-ico"), " ", t("push_title")),
    h("p", {}, pushState === "on" ? t("push_active") : pushState === "denied" ? t("push_denied")
      : pushState === "unsupported" ? t("push_unsupported") : t("push_inactive")),
    h("div", { class: "app-row" },
      pushState === "off" ? h("button", { class: "btn primary", type: "button", onclick: async () => {
        try { await pushOn(); flash = { kind: "ok", text: t("push_active") }; } catch (e) { flash = { kind: "err", text: e.code === "denied" ? t("push_denied") : t("push_err") }; }
        await refreshPush(); render();
      } }, t("push_on")) : null,
      pushState === "on" ? h("button", { class: "btn", type: "button", onclick: async () => {
        try { await api("POST", "/api/v1/public/card/push/test"); flash = { kind: "ok", text: t("push_sent") }; } catch (_) { flash = { kind: "err", text: t("push_err") }; }
        render();
      } }, t("push_test")) : null,
      pushState === "on" ? h("button", { class: "btn", type: "button", onclick: async () => { await pushOff(); await refreshPush(); render(); } }, t("push_off")) : null));
  return [
    push,
    !standalone ? h("section", { class: "app-card" }, installEvt
      ? h("button", { class: "btn primary", type: "button", onclick: async () => { installEvt.prompt(); installEvt = null; render(); } }, t("install"))
      : h("p", { class: "small" }, t("install_hint"))) : null,
    h("section", { class: "app-card" }, h("p", { class: "small muted" }, t("privacy")),
      h("button", { class: "btn danger", type: "button", onclick: async () => {
        if (!confirm(t("logout_confirm"))) return;
        await pushOff().catch(() => {});
        try { localStorage.removeItem(STORE); } catch (_) { /* optional */ }
        TOKEN = ""; data = null; render();
      } }, t("logout"))),
  ];
}

// ---------------------------------------------------------------------- Rahmen
function render() {
  L.apply();
  document.querySelectorAll("[data-tab]").forEach((b) => {
    b.textContent = t("t_" + b.dataset.tab);
    b.setAttribute("aria-current", b.dataset.tab === tab ? "page" : "false");
  });
  const view = $("view");
  $("tabs").hidden = !data;
  $("conn").hidden = !data || !stale();
  if (!TOKEN || invalid) {
    view.replaceChildren(h("section", { class: "app-card" }, icon("unknown", "app-ico"),
      h("h1", {}, invalid ? t("invalid") : t("no_link")), invalid ? null : h("p", {}, t("no_link_text"))));
    return;
  }
  if (!data) { view.replaceChildren(h("p", { class: "muted" }, "…")); return; }
  const body = { home, stalls, history, settings }[tab]();
  view.replaceChildren(...[
    flash ? h("p", { class: `note ${flash.kind === "err" ? "err" : "ok"}`, role: "status" }, flash.text) : null,
    ...body.flat(), h("p", { class: "small muted app-updated" }, t("updated", { t: time(new Date(lastOk).toISOString()) }))].filter(Boolean));
}

document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", async () => {
  tab = b.dataset.tab;
  flash = null;
  if (tab === "settings") await refreshPush();
  render();
  $("main").focus?.();
}));
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvt = e; render(); });
window.addEventListener("hashchange", () => { TOKEN = token("bck_", STORE); load(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
setInterval(() => { if (!document.hidden) load(); }, 10000);
setInterval(render, 5000);
refreshPush().finally(load);
