// Ansicht für die Person am Stellplatz (öffentlich, per QR-Code/NFC-Aufkleber, nur lesend + "Problem melden").
// Link: /s#<token> – das Fragment geht nie an Server/Proxys, das Token nur als Header an die API.
// Grundsatz: ohne aktuelle, gültige Daten NIE "frei" anzeigen.
(function () {
  "use strict";

  const I18N = {
    de: {
      reserved: "RESERVIERT", sub_reserved: "Für eine Reservierung freigehalten – noch {m} min.",
      closed_hours: "Geschlossen (außerhalb der Öffnungszeiten).", closed_closure: "Gesperrt: {n}.", closure: "Sperrzeit", opens: "Öffnet {t}.",
      hours: "Öffnungszeiten", always_open: "Immer geöffnet", day_closed: "geschlossen",
      d_mon: "Mo", d_tue: "Di", d_wed: "Mi", d_thu: "Do", d_fri: "Fr", d_sat: "Sa", d_sun: "So",
      tap_closed: "Geschlossen – Einchecken gerade nicht möglich.", tap_reserved: "Der Stellplatz ist reserviert.",
      tap_insufficient_balance: "Guthaben reicht nicht – bitte bei der Betreuung aufladen.", balance: "Guthaben {p}",
      prepaid_hint: "Die Gebühr wird beim Auschecken vom Guthaben Ihrer Karte abgebucht. Aufladen bei der Betreuung.",
      free: "FREI", occupied: "BELEGT", unknown: "STATUS UNBEKANNT",
      sub_free: "Der Stellplatz ist frei.", sub_occupied: "Ein Fahrrad steht im Stellplatz.",
      r_no_data: "Noch keine Messung empfangen.", r_stale: "Letzte Messung ist zu alt.", r_sensor_error: "Sensor meldet einen Fehler.",
      r_connection: "Keine Verbindung – aktueller Zustand unbekannt.",
      invalid: "Dieser Link ist ungültig oder deaktiviert.", maintenance: "Außer Betrieb – bitte einen anderen Stellplatz nutzen.",
      simulated: "SIMULATION", howto: "So parken Sie",
      step1: "Fahrrad vorwärts in den Stellplatz schieben, Vorderrad in die Schiene.",
      step2: "Karte an den Leser rechts vorne halten – das Display zeigt „Eingecheckt“.",
      step3: "Zum Abholen die Karte erneut anhalten – „Ausgecheckt“ und der Betrag erscheinen.",
      prices: "Preise", price_hint: "Abgerechnet wird über Ihre Karte bei der Schule/Organisation. Hier wird nichts bezahlt.",
      m_free: "kostenlos", m_flat: "{p} pro Parkvorgang", m_per_hour: "{p} pro angefangene Stunde", m_per_day: "{p} pro Kalendertag",
      free_min: "die ersten {n} Minuten kostenlos", cap: "höchstens {p} pro Tag",
      session: "Belegt seit {t} · bisher {p}",
      tap_checked_in: "Eingecheckt ✓", tap_checked_out: "Ausgecheckt ✓ · {p}", tap_unknown_card: "Karte unbekannt – bitte bei der Betreuung freischalten lassen.",
      tap_blocked: "Karte gesperrt – bitte an die Betreuung wenden.", tap_occupied_by_other: "Stellplatz ist bereits belegt.",
      tap_open_elsewhere: "Diese Karte ist noch an einem anderen Stellplatz eingecheckt.", tap_maintenance: "Außer Betrieb.",
      alert: "Hinweis: Um {t} wurde eine ungewöhnliche Erschütterung gemessen. Das ist kein Diebstahlnachweis.",
      camera: "Kamera aktiv: Bei ungewöhnlicher Erschütterung wird ein Einzelbild des Stellplatzes aufgenommen und nach kurzer Zeit automatisch gelöscht.",
      report: "Problem melden", report_what: "Was ist los?", cat_damaged: "Etwas ist kaputt", cat_blocked: "Stellplatz blockiert",
      cat_wrong_status: "Anzeige stimmt nicht", cat_other: "Anderes", report_text: "Beschreibung (optional, max. 300 Zeichen)",
      report_privacy: "Bitte keine Namen oder Telefonnummern angeben. Die Meldung geht an die Betreuung des Stellplatzes.",
      send: "Meldung senden", report_ok: "Danke! Die Meldung wurde übermittelt.", report_limit: "Zu viele Meldungen – bitte später erneut versuchen.",
      report_err: "Meldung konnte nicht gesendet werden.",
      privacy: "Keine Kamera-Livebilder, keine Namen. Angezeigt werden nur Zustand, Zeit und Preise.",
    },
    nl: {
      reserved: "GERESERVEERD", sub_reserved: "Vrijgehouden voor een reservering – nog {m} min.",
      closed_hours: "Gesloten (buiten de openingstijden).", closed_closure: "Afgesloten: {n}.", closure: "sluitingsperiode", opens: "Opent {t}.",
      hours: "Openingstijden", always_open: "Altijd open", day_closed: "gesloten",
      d_mon: "ma", d_tue: "di", d_wed: "wo", d_thu: "do", d_fri: "vr", d_sat: "za", d_sun: "zo",
      tap_closed: "Gesloten – inchecken nu niet mogelijk.", tap_reserved: "De fietsplek is gereserveerd.",
      tap_insufficient_balance: "Onvoldoende tegoed – laat uw kaart opwaarderen bij de begeleiding.", balance: "Tegoed {p}",
      prepaid_hint: "Het bedrag wordt bij het uitchecken van het tegoed van uw kaart afgeschreven. Opwaarderen bij de begeleiding.",
      free: "VRIJ", occupied: "BEZET", unknown: "STATUS ONBEKEND",
      sub_free: "De fietsplek is vrij.", sub_occupied: "Er staat een fiets op de fietsplek.",
      r_no_data: "Nog geen meting ontvangen.", r_stale: "Laatste meting is te oud.", r_sensor_error: "Sensor meldt een fout.",
      r_connection: "Geen verbinding – actuele status onbekend.",
      invalid: "Deze link is ongeldig of uitgeschakeld.", maintenance: "Buiten gebruik – gebruik een andere fietsplek.",
      simulated: "SIMULATIE", howto: "Zo parkeert u",
      step1: "Fiets vooruit de fietsplek in duwen, voorwiel in de rail.",
      step2: "Houd uw kaart bij de lezer rechtsvoor – het display toont „Ingecheckt”.",
      step3: "Bij het ophalen de kaart opnieuw bij de lezer houden – „Uitgecheckt” en het bedrag verschijnen.",
      prices: "Prijzen", price_hint: "Afrekenen gebeurt via uw kaart bij de school/organisatie. Hier wordt niets betaald.",
      m_free: "gratis", m_flat: "{p} per parkeersessie", m_per_hour: "{p} per begonnen uur", m_per_day: "{p} per kalenderdag",
      free_min: "de eerste {n} minuten gratis", cap: "maximaal {p} per dag",
      session: "Bezet sinds {t} · tot nu toe {p}",
      tap_checked_in: "Ingecheckt ✓", tap_checked_out: "Uitgecheckt ✓ · {p}", tap_unknown_card: "Kaart onbekend – laat hem vrijgeven door de begeleiding.",
      tap_blocked: "Kaart geblokkeerd – neem contact op met de begeleiding.", tap_occupied_by_other: "Fietsplek is al bezet.",
      tap_open_elsewhere: "Deze kaart is nog bij een andere fietsplek ingecheckt.", tap_maintenance: "Buiten gebruik.",
      alert: "Let op: om {t} is een ongewone trilling gemeten. Dit is geen bewijs van diefstal.",
      camera: "Camera actief: bij een ongewone trilling wordt één foto van de fietsplek gemaakt en na korte tijd automatisch verwijderd.",
      report: "Probleem melden", report_what: "Wat is er aan de hand?", cat_damaged: "Iets is kapot", cat_blocked: "Fietsplek geblokkeerd",
      cat_wrong_status: "Weergave klopt niet", cat_other: "Anders", report_text: "Beschrijving (optioneel, max. 300 tekens)",
      report_privacy: "Vermeld geen namen of telefoonnummers. De melding gaat naar de begeleiding van de fietsplek.",
      send: "Melding versturen", report_ok: "Bedankt! De melding is verstuurd.", report_limit: "Te veel meldingen – probeer het later opnieuw.",
      report_err: "Melding kon niet worden verstuurd.",
      privacy: "Geen live camerabeelden, geen namen. Alleen status, tijd en prijzen worden getoond.",
    },
    en: {
      reserved: "RESERVED", sub_reserved: "Held for a reservation – {m} min left.",
      closed_hours: "Closed (outside opening hours).", closed_closure: "Closed: {n}.", closure: "closure period", opens: "Opens {t}.",
      hours: "Opening hours", always_open: "Always open", day_closed: "closed",
      d_mon: "Mon", d_tue: "Tue", d_wed: "Wed", d_thu: "Thu", d_fri: "Fri", d_sat: "Sat", d_sun: "Sun",
      tap_closed: "Closed – check-in not possible right now.", tap_reserved: "This stall is reserved.",
      tap_insufficient_balance: "Insufficient balance – please top up with staff.", balance: "Balance {p}",
      prepaid_hint: "The fee is deducted from your card balance when you check out. Top up with staff.",
      free: "FREE", occupied: "OCCUPIED", unknown: "STATUS UNKNOWN",
      sub_free: "The stall is free.", sub_occupied: "A bicycle is in the stall.",
      r_no_data: "No measurement received yet.", r_stale: "Last measurement is too old.", r_sensor_error: "Sensor reports a fault.",
      r_connection: "No connection – current status unknown.",
      invalid: "This link is invalid or disabled.", maintenance: "Out of service – please use another stall.",
      simulated: "SIMULATION", howto: "How to park",
      step1: "Push your bike forwards into the stall, front wheel into the rail.",
      step2: "Hold your card to the reader at the front right – the display shows “Checked in”.",
      step3: "To collect, hold the card to the reader again – “Checked out” and the amount appear.",
      prices: "Prices", price_hint: "Fees are charged to your card via the school/organisation. Nothing is paid here.",
      m_free: "free", m_flat: "{p} per session", m_per_hour: "{p} per started hour", m_per_day: "{p} per calendar day",
      free_min: "first {n} minutes free", cap: "at most {p} per day",
      session: "Occupied since {t} · so far {p}",
      tap_checked_in: "Checked in ✓", tap_checked_out: "Checked out ✓ · {p}", tap_unknown_card: "Unknown card – please have it activated by staff.",
      tap_blocked: "Card blocked – please contact staff.", tap_occupied_by_other: "The stall is already occupied.",
      tap_open_elsewhere: "This card is still checked in at another stall.", tap_maintenance: "Out of service.",
      alert: "Note: an unusual vibration was measured at {t}. This is not proof of theft.",
      camera: "Camera active: on unusual vibration a single picture of the stall is taken and deleted automatically after a short time.",
      report: "Report a problem", report_what: "What is wrong?", cat_damaged: "Something is broken", cat_blocked: "Stall blocked",
      cat_wrong_status: "Display is wrong", cat_other: "Other", report_text: "Description (optional, max. 300 characters)",
      report_privacy: "Please do not include names or phone numbers. The report goes to the staff responsible for this stall.",
      send: "Send report", report_ok: "Thank you! The report has been sent.", report_limit: "Too many reports – please try again later.",
      report_err: "The report could not be sent.",
      privacy: "No live camera images, no names. Only status, time and prices are shown.",
    },
  };
  const TOKEN = decodeURIComponent(location.hash.replace(/^#/, "")).trim();
  const NS = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"';
  const ICONS = {
    free: `<svg ${NS}><circle cx="24" cy="24" r="20" fill="none" stroke="currentColor" stroke-width="4"/><path d="M14 25l7 7 13-15" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    occupied: `<svg ${NS}><circle cx="12" cy="32" r="8" fill="none" stroke="currentColor" stroke-width="3.5"/><circle cx="36" cy="32" r="8" fill="none" stroke="currentColor" stroke-width="3.5"/><path d="M12 32l8-14h11l5 14M20 18l7 14h-15M31 18l-2-6h5M17 13h6" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    reserved: `<svg ${NS}><circle cx="24" cy="27" r="16" fill="none" stroke="currentColor" stroke-width="4"/><path d="M24 18v9l6 5" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/><path d="M18 5h12M24 5v6" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>`,
    unknown: `<svg ${NS}><circle cx="24" cy="24" r="20" fill="none" stroke="currentColor" stroke-width="4" stroke-dasharray="7 4"/><path d="M18 19a6 6 0 1 1 8.4 5.5c-1.6.8-2.4 2-2.4 3.5v1.5" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><circle cx="24" cy="35.5" r="2.8" fill="currentColor"/></svg>`,
  };
  const parser = new DOMParser();
  const $ = (id) => document.getElementById(id);
  let lang = pick();
  let last = null;
  let lastOk = 0;
  let pollMs = 3000;
  let shown = null;

  function pick() {
    try { const s = localStorage.getItem("lang"); if (s && I18N[s]) return s; } catch (_) {}
    const n = (navigator.language || "de").slice(0, 2);
    return I18N[n] ? n : "de";
  }
  const t = (k, v) => {
    let s = I18N[lang][k] ?? I18N.de[k] ?? k;
    if (v) for (const [a, b] of Object.entries(v)) s = s.split(`{${a}}`).join(String(b));
    return s;
  };
  const eur = (c) => new Intl.NumberFormat(lang, { style: "currency", currency: "EUR" }).format((c || 0) / 100);
  const time = (iso) => (iso ? new Date(iso).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" }) : "–");

  function applyLang() {
    document.documentElement.lang = lang;
    document.querySelectorAll("[data-i18n]").forEach((n) => (n.textContent = t(n.dataset.i18n)));
    document.querySelectorAll("[data-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
    shown = null;
    render();
  }
  document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => {
    lang = b.dataset.lang;
    try { localStorage.setItem("lang", lang); } catch (_) {}
    applyLang();
  }));

  function tariffText(tf) {
    if (!tf || tf.mode === "free") return t("m_free");
    const parts = [t("m_" + tf.mode, { p: eur(tf.price_cents) })];
    if (tf.free_minutes) parts.push(t("free_min", { n: tf.free_minutes }));
    if (tf.daily_cap_cents) parts.push(t("cap", { p: eur(tf.daily_cap_cents) }));
    return parts.join(" · ");
  }

  async function poll() {
    if (!TOKEN) { $("invalid").hidden = false; return; }
    try {
      const r = await fetch("/api/v1/public/stall/status", { cache: "no-store", credentials: "omit", headers: { "X-Stall-Token": TOKEN } });
      if (r.status === 404) { $("invalid").hidden = false; last = null; setTimeout(poll, 30000); render(); return; }
      if (r.ok) {
        $("invalid").hidden = true;
        last = await r.json();
        lastOk = Date.now();
        pollMs = Math.max(2000, (last.poll_interval_s || 2) * 1000);
      }
    } catch (_) { /* sichtbar über STATUS UNBEKANNT */ }
    render();
    setTimeout(poll, pollMs);
  }

  function render() {
    const stale = !last || Date.now() - lastOk > (last.stale_after_s || 30) * 1000;
    const state = stale ? "unknown" : last.state;
    const reason = stale ? "connection" : last.unknown_reason || "no_data";
    if (last) {
      $("name").textContent = last.display_name;
      $("location").textContent = last.location || "";
      document.title = last.display_name;
    }
    $("status").dataset.state = state;
    $("word").textContent = t(state);
    const mins = last && last.reservation ? Math.max(1, Math.ceil((new Date(last.reservation.until) - Date.now()) / 60000)) : 0;
    $("sub").textContent = state === "unknown" ? t("r_" + reason) : state === "reserved" ? t("sub_reserved", { m: mins }) : t("sub_" + state);
    if (shown !== state) {
      shown = state;
      $("status-icon").replaceChildren(document.importNode(parser.parseFromString(ICONS[state], "image/svg+xml").documentElement, true));
      $("status-icon").firstChild.setAttribute("class", "sbb-status__icon");
      $("live").textContent = `${t(state)}. ${$("sub").textContent}`;
    }
    $("sim").hidden = !(last && last.simulated_data);
    $("maint").hidden = !(last && last.maintenance);
    const closed = !stale && last && last.closed;
    $("closed").hidden = !closed;
    if (closed) {
      const opens = closed.opens_at ? new Date(closed.opens_at).toLocaleString(lang, { weekday: "short", hour: "2-digit", minute: "2-digit" }) : null;
      $("closed").textContent = (closed.reason === "closure" ? t("closed_closure", { n: closed.note || t("closure") }) : t("closed_hours")) +
        (opens ? " " + t("opens", { t: opens }) : "");
    }
    $("hours-box").hidden = !last;
    if (last) {
      const days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
      $("hours").replaceChildren(...(last.hours ? days.flatMap((d) => {
        const dt = document.createElement("dt"); dt.textContent = t("d_" + d);
        const dd = document.createElement("dd");
        dd.textContent = (last.hours[d] || []).map((r) => `${r[0]}–${r[1]}`).join(", ") || t("day_closed");
        return [dt, dd];
      }) : [Object.assign(document.createElement("dd"), { textContent: t("always_open") })]));
    }
    $("price-hint").textContent = last && last.prepaid ? t("prepaid_hint") : t("price_hint");
    $("howto").hidden = !(last && last.nfc);
    $("camera").hidden = !(last && last.camera_active);
    $("price").textContent = last ? tariffText(last.tariff) : "–";
    const s = !stale && last && last.session;
    $("session").hidden = !s;
    if (s) $("session").textContent = t("session", { t: time(s.started_at), p: eur(s.amount_cents) });
    const tap = !stale && last && last.last_tap;
    $("tap").hidden = !tap || tap.result === "duplicate" || tap.result === "expired";
    if (tap) $("tap").textContent = t("tap_" + tap.result, { p: eur(tap.amount_cents) }) +
      (tap.balance_cents !== null && tap.balance_cents !== undefined ? " · " + t("balance", { p: eur(tap.balance_cents) }) : "");
    const a = !stale && last && last.alert;
    $("alert").hidden = !a;
    if (a) $("alert").textContent = t("alert", { t: time(a.occurred_at) });
  }

  $("report-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const res = $("report-result");
    const cat = document.querySelector('input[name="cat"]:checked').value;
    try {
      const r = await fetch("/api/v1/public/stall/report", { method: "POST", credentials: "omit",
        headers: { "Content-Type": "application/json", "X-Stall-Token": TOKEN },
        body: JSON.stringify({ category: cat, text: $("report-text").value.trim() }) });
      res.textContent = r.ok ? t("report_ok") : r.status === 429 ? t("report_limit") : t("report_err");
      if (r.ok) $("report-text").value = "";
    } catch (_) { res.textContent = t("report_err"); }
    res.hidden = false;
  });

  window.addEventListener("hashchange", () => location.reload());
  setInterval(render, 1000);
  applyLang();
  poll();
})();
