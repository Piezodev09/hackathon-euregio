// Betrieb: Übersicht, Stellplätze, Live-Ansicht, Einstellungen, Meldungen.
// Eine Station ist genau ein vorne offener Stellplatz.
import { get, post, patch, put, del, describeError } from "./api.js";
import { getLang, t } from "./i18n.js";
import { el, clear, field, toast, confirmDialog, fmtDateTime, fmtTime, copyText, icon, effectiveState, reasonText,
  stallStatus, stallBadge, fmtAge, fmtCents, closedText, remainingMin } from "./ui.js";
import { state, can, every, go } from "./state.js";
import { sessionSummary, tariffForm, tariffText } from "./views-parking.js";
import { onboardingCard, reservationBox, hoursCard, createDemoStation } from "./views-more.js";

let devicesTimer = null;
const errorCard = (e) => el("div", { class: "alert-box error", role: "alert" }, describeError(e));

function kpi(value, label) {
  return el("div", { class: "card kpi" }, el("span", { class: "value" }, String(value)), el("span", { class: "label" }, label));
}

function stallCard(s) {
  const live = s.live || {};
  return el("article", { class: "card stall-card" },
    el("h3", {}, el("a", { href: `#/stations/${s.id}` }, s.name)),
    s.location ? el("p", { class: "muted small" }, s.location) : null,
    stallBadge(live.state || "unknown"),
    el("div", { class: "stall-meta" },
      live.state === "unknown" && live.unknown_reason ? el("span", {}, reasonText(live.unknown_reason)) :
        live.age_s !== null && live.age_s !== undefined ? el("span", {}, t("st.age", { s: fmtAge(live.age_s) })) : null,
      live.alert ? el("span", { class: "badge warn" }, icon("vib"), t("st.alert")) : null,
      live.maintenance ? el("span", { class: "badge" }, icon("wrench"), t("ss.maintenance")) : null,
      live.closed ? el("span", { class: "badge" }, icon("lock"), t("st.closed")) : null,
      live.reservation ? el("span", { class: "badge res" }, icon("reserved"), t("res.left", { m: remainingMin(live.reservation) })) : null,
      s.demo ? el("span", { class: "badge sim" }, t("ob.demo_badge")) : null,
      live.session ? el("span", { class: "badge ok" }, icon("card"), t("pk.checked_in"), " · ", fmtCents(live.session.amount_cents)) : null,
      live.simulated_data ? el("span", { class: "badge sim" }, t("st.sim")) : null));
}

// ---------------------------------------------------------------------- Übersicht
function emptyOverview() {
  return el("div", { class: "card empty-state" }, icon("logo", "empty-logo"),
    el("h2", {}, t("ov.empty_title")), el("p", {}, t("ov.empty_text")),
    can("admin") ? el("div", { class: "btn-row" },
      state.me.demo_stalls ? el("button", { class: "btn primary", type: "button", onclick: () => createDemoStation() }, icon("play"), t("ob.demo_btn")) : null,
      el("a", { class: "btn", href: "#/stations/new" }, t("ov.create"))) : null);
}

export function viewOverview() {
  const kpis = el("div", { class: "grid cols-4", "data-tour": "kpis" });
  const cards = el("div", { class: "grid cols-3" });
  const onboarding = onboardingCard();
  const node = el("div", { class: "page-stack" }, onboarding, kpis, el("h2", { class: "visually-hidden" }, t("nav.stations")), cards);
  every(5000, async () => {
    try {
      const [{ stations }, { events }] = await Promise.all([get("/api/v1/stations"), get("/api/v1/events?open_only=true&limit=100")]);
      const count = (st) => stations.filter((s) => (s.live?.state || "unknown") === st).length;
      // Tageswerte rechnet der Server (Tagesgrenze Europe/Berlin, auch über Monatsgrenzen hinweg)
      let today = { checkins: "–", revenue: "–" };
      try {
        const d = await get("/api/v1/stats/today");
        today = { checkins: d.checkins, revenue: fmtCents(d.fees_cents) };
      } catch (_) { /* Kennzahlen optional */ }
      clear(kpis, kpi(stations.length, t("ov.stations")), kpi(count("free"), t("ov.state_free")),
        kpi(count("unknown"), t("ov.state_unknown")), kpi(events.length, t("ov.alerts")),
        kpi(stations.filter((s) => s.live?.session).length, t("ov.parked_now")), kpi(today.checkins, t("ov.checkins_today")),
        kpi(today.revenue, t("ov.revenue_today")));
      clear(cards, stations.length ? stations.map(stallCard) : emptyOverview());
    } catch (e) { clear(cards, errorCard(e)); }
  });
  return node;
}

export function viewStations() {
  const body = el("div", {}, el("p", {}, t("c.loading")));
  get("/api/v1/stations").then(({ stations }) => {
    clear(body, stations.length ? el("div", { class: "card table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, [t("ss.name"), t("ss.location"), t("c.status"), t("ss.display"), t("c.actions")].map((h) => el("th", { scope: "col" }, h)))),
      el("tbody", {}, stations.map((s) => el("tr", {},
        el("td", {}, el("a", { href: `#/stations/${s.id}` }, s.name)), el("td", {}, s.location || "–"),
        el("td", {}, stallBadge(s.live?.state || "unknown")),
        el("td", {}, s.display_enabled ? el("span", { class: "badge ok" }, t("ss.active")) : "–"),
        el("td", {}, el("div", { class: "btn-row" }, el("a", { class: "btn small", href: `#/stations/${s.id}` }, t("ov.open")),
          can("admin") ? el("a", { class: "btn small", href: `#/stations/${s.id}/settings` }, t("st.settings")) : null))))))) :
      el("div", { class: "card" }, el("p", {}, t("ov.empty"))));
  }).catch((e) => clear(body, errorCard(e)));
  return body;
}

export function viewNewStation() {
  const err = el("div", { role: "alert" });
  const name = el("input", { type: "text", name: "name", required: true, maxlength: "100", autofocus: true });
  const loc = el("input", { type: "text", name: "location", maxlength: "200" });
  const form = el("form", { class: "card" }, err, el("p", { class: "muted" }, t("ss.new_hint")),
    field(t("ss.name"), name), field(t("ss.location"), loc),
    el("button", { class: "btn primary", type: "submit" }, t("ss.create")));
  const demo = state.me.demo_stalls ? el("section", { class: "card ob-demo" }, icon("play"),
    el("div", {}, el("p", {}, el("strong", {}, t("ob.demo_title"))), el("p", { class: "small" }, t("ob.demo_text"))),
    el("button", { class: "btn", type: "button", onclick: () => createDemoStation() }, t("ob.demo_btn"))) : null;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const st = await post("/api/v1/stations", { name: name.value, location: loc.value });
      go(`/stations/${st.id}/settings`);
    } catch (e) { clear(err, errorCard(e)); }
  });
  return el("div", { class: "page-stack" }, form, demo);
}

// ---------------------------------------------------------------------- Live-Ansicht
export function usageChart(summary) {
  const first = summary.buckets.findIndex((b) => b.occupancy !== null);
  const buckets = first < 0 ? [] : summary.buckets.slice(first);
  if (!buckets.length) return el("p", { class: "muted" }, t("st.usage_none"));
  const hourOf = (iso) => String(new Date(iso).getHours()).padStart(2, "0");
  const top = buckets.filter((b) => b.occupancy !== null).reduce((a, b) => (b.occupancy > a.occupancy ? b : a));
  return el("div", {},
    el("p", {}, t("st.usage_text", { h: hourOf(top.hour_start), v: Math.round(top.occupancy * 100).toLocaleString(getLang()) })),
    el("ul", { class: "usage-list", "aria-label": t("st.usage") }, buckets.map((b) => {
      const v = b.occupancy;
      const bar = el("span");
      bar.style.width = v === null ? "0%" : `${Math.round(v * 100)}%`;
      return el("li", { class: v === null ? "nodata" : null },
        el("span", { class: "hour" }, `${hourOf(b.hour_start)}:00`),
        el("span", { class: "meter", "aria-hidden": "true" }, bar),
        el("span", { class: "val" }, v === null ? "–" : `${Math.round(v * 100)} %`));
    })),
    el("p", { class: "small muted" }, t("st.usage_hint")),
    summary.contains_simulated ? el("p", {}, el("span", { class: "badge sim" }, t("st.usage_sim"))) : null);
}

function eventsTable(events, reload, { showStation = true } = {}) {
  if (!events.length) return el("p", { class: "muted" }, t("ev.none"));
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, [t("ev.time"), showStation ? t("ev.station") : null, t("ev.kind"), t("ev.detector"), t("ev.ack")]
      .filter(Boolean).map((h) => el("th", { scope: "col" }, h)))),
    el("tbody", {}, events.map((e) => {
      let ack = "–";
      if (e.acknowledged_at) ack = `${fmtDateTime(e.acknowledged_at)} · ${e.acknowledged_by || ""}`;
      else if (e.severity === "warning" && can("operator")) {
        ack = el("button", { class: "btn small", type: "button", onclick: async () => {
          try { await post(`/api/v1/events/${e.id}/ack`); reload(); } catch (err) { toast(describeError(err), "error"); }
        } }, t("ev.ack_btn"));
      }
      return el("tr", { class: e.severity },
        el("td", {}, fmtDateTime(e.occurred_at)), showStation ? el("td", {}, e.station_name) : null,
        el("td", {}, t("ev.k_" + e.kind), e.simulated ? [" ", el("span", { class: "badge sim" }, t("c.simulated"))] : null,
          e.kind === "user_report" && e.detail ? el("div", { class: "small muted" }, t("sv.cat_" + e.detail.category), e.detail.text ? ": " + e.detail.text : "") : null),
        el("td", {}, e.detector ? t("ev.d_" + e.detector) + (e.severity === "shadow" ? " " + t("ev.shadow") : "") : "–"),
        el("td", {}, ack));
    }))));
}

function warningItem(kind, title, text, action) {
  return el("li", { class: `sbb-alert sbb-alert--${kind}` }, icon(kind === "warn" ? "warn" : "info"),
    el("p", { class: "sbb-alert__title" }, title), el("p", { class: "sbb-alert__text" }, text),
    action ? el("div", { class: "sbb-alert__actions" }, action) : null);
}

export function viewStation(id, setTitle) {
  const statusBox = el("div");
  const sessionBox = el("div");
  const resBox = el("div");
  let resSig = "";
  const live = el("p", { class: "visually-hidden", "aria-live": "polite" });
  const measure = el("section", { class: "card", "aria-labelledby": "measure-h" });
  const warnings = el("section", { class: "card", "aria-labelledby": "warn-h" });
  const ai = el("section", { class: "card" });
  const usage = el("section", { class: "card" });
  const recent = el("section", { class: "card" });
  let lastOk = 0;
  let last = null;
  let lastState = null;
  let warnSig = "";

  const node = el("div", { class: "stall-grid" },
    el("div", {}, el("section", { "aria-labelledby": "status-h" }, el("h2", { id: "status-h", class: "visually-hidden" }, t("st.current")), statusBox, sessionBox, resBox, live), measure, usage),
    el("div", {}, warnings, ai, recent));

  const render = () => {
    if (!last) return;
    const stale = last.stale_after_s || 30;
    const connLost = Date.now() - lastOk > stale * 1000;
    const { state: st, reason } = effectiveState(last, connLost);
    clear(statusBox, last.maintenance ? el("div", { class: "maint-banner", role: "status" }, icon("wrench"), t("st.maintenance")) : null,
      last.closed ? el("div", { class: "maint-banner closed", role: "status" }, icon("lock"), closedText(last.closed)) : null,
      stallStatus(last, { connLost, simulated: last.simulated_data }));
    clear(sessionBox, sessionSummary(last.session));
    const rs = JSON.stringify([last.reservation?.id, last.reservation && remainingMin(last.reservation), last.state, !!last.closed, last.maintenance, getLang()]);
    if (rs !== resSig && !resBox.querySelector("form")) { resSig = rs; clear(resBox, reservationBox(id, last, () => { resSig = ""; poll(); })); }
    if (st !== lastState) {
      lastState = st;
      live.textContent = `${t("st.current")}: ${t("st." + st)}. ${st === "unknown" ? reasonText(reason, stale) : ""}`;
    }

    // Messung
    const age = last.age_s === null ? null : last.age_s + (Date.now() - lastOk) / 1000;
    const isStale = age === null || age > stale;
    const fill = el("div", { class: "sbb-age__fill" });
    fill.style.width = age === null ? "0%" : `${Math.min(100, (age / stale) * 100).toFixed(1)}%`;
    clear(measure, el("h2", { id: "measure-h", class: "sbb-card__title" }, t("st.measure")),
      el("dl", { class: "sbb-meta" },
        el("div", {}, el("dt", {}, t("st.last")), el("dd", { class: "mono" }, fmtTime(last.last_update),
          el("small", {}, age === null ? t("c.never") : t("st.ago", { s: fmtAge(age) })))),
        el("div", {}, el("dt", {}, t("st.source")), el("dd", {},
          last.last_update ? (last.simulated_data ? t("st.src_simulator") : t("st.src_sensor")) : t("st.src_none"),
          last.simulated_data ? el("small", {}, t("st.sim_long")) : null))),
      el("div", { class: "sbb-age", "data-stale": String(isStale && age !== null) },
        el("div", { class: "sbb-age__track", "aria-hidden": "true" }, fill),
        el("div", { class: "sbb-age__text" },
          el("span", {}, age === null ? "–" : `${t("st.age", { s: fmtAge(age) })} – ${isStale ? t("st.age_stale") : t("st.age_ok")}`),
          el("span", {}, t("st.valid", { s: stale })))));

    // Technische Warnungen – nur bei Änderung neu zeichnen (Fokus auf „Quittieren“ bleibt erhalten)
    const items = [];
    if (connLost) items.push(["warn", t("st.w_conn"), t("st.w_conn_text")]);
    else if (reason === "stale") items.push(["warn", t("st.w_stale"), t("st.w_stale_text", { s: stale })]);
    else if (reason === "sensor_error") items.push(["warn", t("st.w_sensor"), t("st.w_sensor_text")]);
    else if (reason === "no_data") items.push(["info", t("st.w_nodata"), t("st.w_nodata_text")]);
    const a = last.alert;
    const sig = JSON.stringify([items, a && a.id, getLang()]);
    if (sig !== warnSig) {
      warnSig = sig;
      const list = items.map(([k, ti, tx]) => warningItem(k, ti, tx));
      if (a) {
        list.push(el("li", { class: "sbb-alert sbb-alert--warn" }, icon("vib"),
          el("p", { class: "sbb-alert__title" }, t("st.w_movement", { t: fmtTime(a.occurred_at) }), a.simulated ? [" ", el("span", { class: "badge sim" }, t("c.simulated"))] : null),
          el("p", { class: "sbb-alert__text" }, t("st.w_movement_text")),
          el("div", { class: "sbb-alert__actions btn-row" },
            can("operator") && a.id ? el("button", { class: "btn small", type: "button", onclick: async () => {
              try { await post(`/api/v1/events/${a.id}/ack`); await poll(); loadRecent(); warnings.querySelector("h2")?.focus(); } catch (e) { toast(describeError(e), "error"); }
            } }, t("ev.ack_btn")) : null,
            last.camera_active && can("admin") ? el("button", { class: "btn small", type: "button", onclick: async (ev) => {
              const holder = ev.target.closest(".sbb-alert").querySelector(".snapshot") || el("div", { class: "snapshot" });
              ev.target.closest(".sbb-alert").append(holder);
              try {
                const { snapshots } = await get(`/api/v1/stations/${id}/snapshots`);
                const s = snapshots.find((x) => x.event_id === a.id) || null;
                clear(holder, s ? el("img", { src: `/api/v1/snapshots/${s.id}`, alt: t("cam.img_alt") }) : el("p", { class: "small" }, t("cam.none_yet")));
              } catch (e) { toast(describeError(e), "error"); }
            } }, icon("camera"), t("cam.show")) : null)));
      }
      clear(warnings, el("h2", { id: "warn-h", class: "sbb-card__title", tabindex: "-1" }, t("st.warnings"), " ",
        el("span", { class: "badge planned" }, t("st.warn_count", { n: list.length }))),
        el("ul", { class: "sbb-alerts" }, list.length ? list : el("li", { class: "sbb-alert sbb-alert--ok" }, icon("ok"),
          el("p", { class: "sbb-alert__title" }, t("st.warn_none")))));
    }

    const m = last.ai;
    clear(ai, el("h2", {}, t("st.ai")),
      el("p", {}, m.visible_detector === "ml" ? t("st.ai_ml") : t("st.ai_rule")),
      el("p", { class: "small" }, !m.plan_allows_ml ? t("st.ai_plan") : m.model_available ? t("st.ai_ok") : t("st.ai_off")),
      m.model_trained_on_simulated_data ? el("p", {}, el("span", { class: "badge sim" }, t("st.ai_sim"))) : null,
      el("p", { class: "small muted" }, t("st.disclaimer")));
  };

  const poll = async () => {
    try {
      last = await get(`/api/v1/stations/${id}/status`);
      lastOk = Date.now();
      setTitle(last.display_name);
    } catch (e) {
      if (!last) clear(statusBox, errorCard(e));
    }
    render();
  };
  const loadUsage = async () => {
    try { clear(usage, el("h2", {}, t("st.usage")), usageChart(await get(`/api/v1/stations/${id}/occupancy?hours=24`))); } catch (_) {}
  };
  const loadRecent = async () => {
    try {
      const { events } = await get(`/api/v1/events?station_id=${encodeURIComponent(id)}&limit=10`);
      clear(recent, el("h2", {}, t("st.recent")), eventsTable(events, loadRecent, { showStation: false }),
        el("a", { href: "#/events" }, t("nav.events") + " →"));
    } catch (_) {}
  };
  every(state.me?.tenant ? 2000 : 5000, poll);
  every(1000, render);
  setTimeout(() => { every(60000, loadUsage); every(10000, loadRecent); }, 300);
  return node;
}

// ---------------------------------------------------------------------- Einstellungen
export function viewStationSettings(id, setTitle) {
  const node = el("div", {}, el("p", {}, t("c.loading")));
  const load = async () => {
    try {
      const st = await get(`/api/v1/stations/${id}`);
      setTitle(`${st.name} – ${t("st.settings")}`);
      clear(node, st.demo ? el("div", { class: "alert-box info" }, t("ob.demo_settings")) : null,
        general(st), operationCard(st), hoursCard(st, load), devicesCard(st), stallViewCard(st), displayCard(st), cameraCard(st), dangerCard(st));
    } catch (e) { clear(node, errorCard(e)); }
  };

  function general(st) {
    const name = el("input", { type: "text", value: st.name, required: true, maxlength: "100" });
    const loc = el("input", { type: "text", value: st.location, maxlength: "200" });
    const src = el("select", { disabled: !st.plan_allows_ml },
      el("option", { value: "rule", selected: st.alert_source === "rule" }, t("ss.rule")),
      el("option", { value: "ml", selected: st.alert_source === "ml" }, t("ss.ml")));
    const f = el("form", { class: "card" }, el("h2", {}, t("ss.general")), field(t("ss.name"), name), field(t("ss.location"), loc),
      field(t("ss.alert_source"), src, st.plan_allows_ml ? t("st.disclaimer") : t("st.ai_plan")),
      el("div", { class: "btn-row" }, el("button", { class: "btn primary", type: "submit" }, t("c.save")),
        el("a", { class: "btn", href: `#/stations/${id}` }, t("ov.open"))));
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await patch(`/api/v1/stations/${id}`, { name: name.value, location: loc.value, ...(st.plan_allows_ml ? { alert_source: src.value } : {}) });
        toast(t("c.saved"));
        load();
      } catch (e) { toast(describeError(e), "error"); }
    });
    return f;
  }

  function operationCard(st) {
    const maint = el("input", { type: "checkbox", checked: st.maintenance });
    maint.addEventListener("change", async () => {
      try { await patch(`/api/v1/stations/${id}`, { maintenance: maint.checked }); toast(t("c.saved")); }
      catch (e) { maint.checked = !maint.checked; toast(describeError(e), "error"); }
    });
    const billingOn = !!state.me.tenant.plan.parking_billing;
    return el("section", { class: "card" }, el("h2", {}, t("ss.operation")),
      el("label", { class: "check" }, maint, el("span", {}, t("ss.maintenance"), el("small", { class: "hint" }, t("ss.maintenance_hint")))),
      el("h3", {}, t("pk.tariff")),
      el("p", {}, st.tariff ? tariffText(st.tariff) : t("pk.inherited")),
      billingOn ? tariffForm(st.tariff, async (tf) => {
        try { await put(`/api/v1/stations/${id}/tariff`, { tariff: tf }); toast(t("c.saved")); load(); } catch (e) { toast(describeError(e), "error"); }
      }, { allowInherit: true }) : el("p", { class: "small muted" }, t("pk.feature_off", { f: t("feat.parking_billing") })));
  }

  function devicesCard(st) {
    const card = el("section", { class: "card" }, el("h2", {}, t("ag.title")), el("p", { class: "muted" }, t("ag.hint")));
    const list = el("div", {}, t("c.loading"));
    const pending = el("div");
    const reveal = el("div");

    const autoUpd = el("input", { type: "checkbox", checked: st.auto_update });
    autoUpd.addEventListener("change", async () => {
      try { await patch(`/api/v1/stations/${id}`, { auto_update: autoUpd.checked }); toast(t("c.saved")); }
      catch (e) { autoUpd.checked = !autoUpd.checked; toast(describeError(e), "error"); }
    });

    const setup = el("button", { class: "btn primary", type: "button", onclick: async () => {
      try {
        const r = await post(`/api/v1/stations/${id}/enrollments`, { name: "Pi-Gateway" });
        clear(reveal, enrollBox(r));
        loadAll();
      } catch (e) { toast(describeError(e), "error"); }
    } }, "+ " + t("ag.setup"));

    const command = async (d, cmd, confirmText) => {
      if (confirmText && !(await confirmDialog(confirmText))) return;
      try { await post(`/api/v1/stations/${id}/devices/${d.id}/command`, { command: cmd }); toast(t("ag.queued")); loadAll(); }
      catch (e) { toast(describeError(e), "error"); }
    };

    const loadAll = async () => {
      try {
        const [{ devices, latest_version }, { enrollments }] = await Promise.all([
          get(`/api/v1/stations/${id}/devices`), get(`/api/v1/stations/${id}/enrollments`)]);
        clear(list, devices.length ? el("div", { class: "table-wrap" }, el("table", {},
          el("thead", {}, el("tr", {}, [t("ss.device_name"), t("c.status"), t("ag.version"), t("ag.health"), t("ag.last_contact"), t("c.actions")]
            .map((h) => el("th", { scope: "col" }, h)))),
          el("tbody", {}, devices.map((d) => el("tr", {},
            el("td", {}, el("strong", {}, d.name), d.hostname ? el("div", { class: "small muted mono" }, d.hostname) : null,
              el("div", { class: "small muted mono" }, d.token_prefix + "…")),
            el("td", {}, deviceStatus(d)),
            el("td", {}, d.agent_version || "–", d.update_available ? el("div", {}, el("span", { class: "badge warn" }, t("ag.update_avail", { v: latest_version }))) : null),
            el("td", { class: "small" }, healthSummary(d)),
            el("td", { class: "small" }, fmtDateTime(d.last_heartbeat_at || d.last_seen_at)),
            el("td", {}, d.revoked_at ? "–" : el("div", { class: "btn-row" },
              d.managed ? el("button", { class: "btn small", type: "button", onclick: () => command(d, "identify") }, t("ag.identify")) : null,
              d.managed ? el("button", { class: "btn small", type: "button", onclick: () => command(d, "restart") }, t("ag.restart")) : null,
              d.managed ? el("button", { class: "btn small", type: "button", onclick: () => command(d, "rotate_token", t("ag.rotate_confirm")) }, t("ag.rotate")) : null,
              d.managed && d.update_available ? el("button", { class: "btn small", type: "button", onclick: () => command(d, "update") }, t("ag.update")) : null,
              el("button", { class: "btn small danger", type: "button", onclick: async () => {
                if (!(await confirmDialog(t("ag.revoke_confirm", { name: d.name }), { danger: true }))) return;
                try { await del(`/api/v1/stations/${id}/devices/${d.id}`); loadAll(); } catch (e) { toast(describeError(e), "error"); }
              } }, t("ss.revoke"))))))))) : el("p", { class: "muted" }, t("ag.none")));
        clear(pending, enrollments.length ? el("div", {}, el("h3", {}, t("ag.pending")), el("ul", {}, enrollments.map((e) =>
          el("li", {}, `${e.name} – ${t("ag.code_expires", { t: fmtDateTime(e.expires_at) })} `,
            el("button", { class: "btn small", type: "button", onclick: async () => {
              try { await del(`/api/v1/stations/${id}/enrollments/${e.id}`); loadAll(); } catch (err) { toast(describeError(err), "error"); }
            } }, t("tm.withdraw")))))) : null);
      } catch (e) { clear(list, errorCard(e)); }
    };

    // Manuelles Token (Fortgeschrittene / ohne Installationsskript)
    const name = el("input", { type: "text", required: true, maxlength: "100", value: "Pi-Gateway" });
    const manual = el("form", { class: "btn-row" }, field(t("ss.device_name"), name), el("button", { class: "btn", type: "submit" }, t("ss.add_device")));
    const manualReveal = el("div");
    manual.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        const d = await post(`/api/v1/stations/${id}/devices`, { name: name.value });
        const cfg = `[api]\nurl = "${location.origin}"\n\n[station]\nid = "${d.station_id}"\n\n# /etc/bike-gateway/gateway.env\n# BIKE_DEVICE_TOKEN=${d.token}`;
        clear(manualReveal, el("div", { class: "alert-box warn" }, el("p", {}, t("ss.token_once")),
          el("p", { class: "secret-box mono" }, d.token), el("button", { class: "btn small", type: "button", onclick: () => copyText(d.token) }, t("c.copy"))),
          el("p", { class: "small muted" }, t("ss.gateway_hint")), el("pre", { class: "secret-box mono small" }, cfg),
          el("button", { class: "btn small", type: "button", onclick: () => copyText(cfg) }, t("c.copy")));
        loadAll();
      } catch (e) { toast(describeError(e), "error"); }
    });

    if (devicesTimer) clearInterval(devicesTimer);
    devicesTimer = every(15000, loadAll);
    card.append(el("div", { class: "btn-row" }, setup, el("label", { class: "check" }, autoUpd, el("span", {}, t("ag.auto_update")))),
      reveal, pending, list,
      el("details", { class: "advanced" }, el("summary", {}, t("ag.manual")), el("p", { class: "small muted" }, t("ss.devices_hint")), manual, manualReveal));
    return card;
  }

  function displayCard(st) {
    const reveal = el("div");
    const toggle = el("input", { type: "checkbox", checked: st.display_enabled, disabled: !st.display_configured });
    toggle.addEventListener("change", async () => {
      try { await patch(`/api/v1/stations/${id}`, { display_enabled: toggle.checked }); toast(t("c.saved")); } catch (e) { toast(describeError(e), "error"); toggle.checked = !toggle.checked; }
    });
    const rotate = el("button", { class: "btn", type: "button", onclick: async () => {
      if (st.display_configured && !(await confirmDialog(t("ss.display_rotate") + "?"))) return;
      try {
        const r = await post(`/api/v1/stations/${id}/display-link`);
        st.display_configured = true;
        toggle.disabled = false;
        toggle.checked = true;
        clear(reveal, el("div", { class: "alert-box info" }, el("p", {}, t("ss.display_url")), el("p", { class: "secret-box mono" }, r.url),
          el("div", { class: "btn-row" }, el("button", { class: "btn small", type: "button", onclick: () => copyText(r.url) }, t("c.copy")),
            el("a", { class: "btn small", href: r.url, target: "_blank", rel: "noopener noreferrer" }, t("ss.open_display")))));
      } catch (e) { toast(describeError(e), "error"); }
    } }, st.display_configured ? t("ss.display_rotate") : t("ss.display_create"));
    return el("section", { class: "card" }, el("h2", {}, t("ss.display")), el("p", { class: "muted" }, t("ss.display_hint")),
      el("div", { class: "field" }, el("label", { class: "check" }, toggle, el("span", {}, t("ss.display_on")))), rotate, reveal);
  }

  function stallViewCard(st) {
    const box = el("div");
    const planOk = !!state.me.tenant.plan.stall_view;
    const toggle = el("input", { type: "checkbox", checked: st.stall_view_enabled, disabled: !st.stall_view_configured });
    toggle.addEventListener("change", async () => {
      try { await patch(`/api/v1/stations/${id}`, { stall_view_enabled: toggle.checked }); toast(t("c.saved")); }
      catch (e) { toggle.checked = !toggle.checked; toast(describeError(e), "error"); }
    });
    const show = (url) => {
      const qr = window.qrcode ? (() => { const q = window.qrcode(0, "M"); q.addData(url); q.make(); return q.createDataURL(6, 2); })() : null;
      clear(box, el("div", { class: "alert-box info" }, el("p", {}, t("sv.link_once")), el("p", { class: "secret-box mono" }, url),
        el("div", { class: "btn-row" }, el("button", { class: "btn small", type: "button", onclick: () => copyText(url) }, t("c.copy")),
          el("a", { class: "btn small", href: url, target: "_blank", rel: "noopener noreferrer" }, t("sv.open")),
          qr ? el("button", { class: "btn small", type: "button", onclick: () => printSticker(st.name, qr) }, t("sv.print")) : null)),
        qr ? el("p", {}, el("img", { class: "qr-img", src: qr, alt: t("sv.qr_alt"), width: "200", height: "200" })) : null);
    };
    const rotate = el("button", { class: "btn", type: "button", disabled: !planOk, onclick: async () => {
      if (st.stall_view_configured && !(await confirmDialog(t("sv.rotate_confirm")))) return;
      try {
        const r = await post(`/api/v1/stations/${id}/stall-link`);
        st.stall_view_configured = true; toggle.disabled = false; toggle.checked = true;
        show(r.url);
      } catch (e) { toast(describeError(e), "error"); }
    } }, st.stall_view_configured ? t("sv.rotate") : t("sv.create"));
    return el("section", { class: "card" }, el("h2", {}, t("sv.title")), el("p", { class: "muted" }, t("sv.hint")),
      !planOk ? el("p", { class: "small muted" }, t("pk.feature_off", { f: t("feat.stall_view") })) : null,
      el("div", { class: "field" }, el("label", { class: "check" }, toggle, el("span", {}, t("sv.enabled")))), rotate, box);
  }

  function cameraCard(st) {
    const planOk = !!state.me.tenant.plan.camera;
    const on = el("input", { type: "checkbox", checked: st.camera_enabled, disabled: !planOk });
    const approved = el("input", { type: "text", maxlength: "200", value: st.camera_approved_by || "", placeholder: t("cam.approved_ph") });
    const hours = el("input", { type: "number", min: "1", max: "72", value: String(st.camera_retention_h || 24) });
    const list = el("div", { class: "cam-images" });
    const loadList = async () => {
      if (!st.camera_enabled) { clear(list); return; }
      try {
        const { snapshots } = await get(`/api/v1/stations/${id}/snapshots`);
        clear(list, el("h3", {}, t("cam.images")), snapshots.length ? el("ul", { class: "plain-list" }, snapshots.map((s) => {
          const holder = el("div", { class: "snapshot" });
          return el("li", {}, el("div", { class: "btn-row" }, el("span", {}, fmtDateTime(s.taken_at), " · ", t("cam.r_" + s.reason),
            s.simulated ? [" ", el("span", { class: "badge sim" }, t("c.simulated"))] : null),
            el("button", { class: "btn small", type: "button", onclick: () => clear(holder, el("img", { src: `/api/v1/snapshots/${s.id}`, alt: t("cam.img_alt"), loading: "lazy" })) }, t("cam.show")),
            el("button", { class: "btn small danger", type: "button", onclick: async () => {
              try { await del(`/api/v1/snapshots/${s.id}`); loadList(); } catch (e) { toast(describeError(e), "error"); }
            } }, t("c.delete"))), holder);
        })) : el("p", { class: "muted small" }, t("cam.none")));
      } catch (e) { clear(list, errorCard(e)); }
    };
    const form = el("form", {},
      el("label", { class: "check" }, on, el("span", {}, t("cam.enable"), el("small", { class: "hint" }, t("cam.enable_hint")))),
      el("div", { class: "grid cols-2" }, field(t("cam.approved"), approved, t("cam.approved_hint")), field(t("cam.retention"), hours, t("cam.retention_hint"))),
      el("div", { class: "btn-row" }, el("button", { class: "btn primary", type: "submit", disabled: !planOk }, t("c.save")),
        st.camera_enabled ? el("button", { class: "btn", type: "button", onclick: async () => {
          try { await post(`/api/v1/stations/${id}/camera/snapshot`); toast(t("cam.requested")); } catch (e) { toast(describeError(e), "error"); }
        } }, t("cam.test")) : null));
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!on.checked && st.camera_enabled && !(await confirmDialog(t("cam.disable_confirm"), { danger: true }))) return;
      try {
        await put(`/api/v1/stations/${id}/camera`, { enabled: on.checked, retention_h: parseInt(hours.value, 10) || 24, approved_by: approved.value || null });
        toast(t("c.saved")); load();
      } catch (e) { toast(describeError(e), "error"); }
    });
    loadList();
    return el("section", { class: "card" }, el("h2", {}, t("cam.title")), el("p", { class: "muted" }, t("cam.hint")),
      !planOk ? el("p", { class: "small muted" }, t("pk.feature_off", { f: t("feat.camera") })) : null, form, list);
  }

  function dangerCard(st) {
    return el("section", { class: "card" }, el("h2", {}, t("ss.danger")),
      el("button", { class: "btn danger", type: "button", onclick: async () => {
        if (!(await confirmDialog(t("ss.delete_confirm", { name: st.name }), { danger: true, confirmLabel: t("ss.delete") }))) return;
        try { await del(`/api/v1/stations/${id}`); go("/stations"); } catch (e) { toast(describeError(e), "error"); }
      } }, t("ss.delete")));
  }

  load();
  return node;
}

// ---------------------------------------------------------------------- Gateways
export function deviceStatus(d) {
  if (d.revoked_at) return el("span", { class: "badge err" }, t("ss.revoked"));
  if (!d.managed) return el("span", { class: "badge" }, t("ag.unmanaged"));
  return d.online ? el("span", { class: "badge ok" }, "● " + t("ag.online")) : el("span", { class: "badge err" }, "○ " + t("ag.offline"));
}

export function healthSummary(d) {
  const h = d.health || {};
  if (!d.managed || !d.last_heartbeat_at) return "–";
  const parts = [];
  if (h.serial_connected !== undefined && h.serial_connected !== null) {
    parts.push(el("span", { class: h.serial_connected ? "badge ok" : "badge err" }, (h.serial_connected ? "✓ " : "✗ ") + t("ag.arduino")));
  }
  if (d.source === "simulator") parts.push(el("span", { class: "badge sim" }, t("c.simulated")));
  const facts = [];
  if (h.cpu_temp_c !== undefined && h.cpu_temp_c !== null) facts.push(`${h.cpu_temp_c} °C`);
  if (h.buffer_len) facts.push(t("ag.buffer", { n: h.buffer_len }));
  if (h.disk_free_mb !== undefined && h.disk_free_mb !== null) facts.push(`${Math.round(h.disk_free_mb / 1024 * 10) / 10} GB ${t("ag.free")}`);
  if (h.uptime_s) facts.push(h.uptime_s < 3600 ? t("ag.uptime_m", { m: Math.max(1, Math.round(h.uptime_s / 60)) })
    : t("ag.uptime", { h: Math.round(h.uptime_s / 360) / 10 }));
  return el("div", {}, el("div", { class: "btn-row" }, parts), facts.length ? el("div", { class: "muted" }, facts.join(" · ")) : null,
    h.last_error ? el("div", { class: "small", title: h.last_error }, "⚠ " + h.last_error.slice(0, 80)) : null);
}

const cmdRow = (label, text) => el("div", { class: "field" }, el("div", { class: "small muted" }, label),
  el("div", { class: "btn-row" }, el("code", { class: "secret-box mono small cmd" }, text),
    el("button", { class: "btn small", type: "button", onclick: () => copyText(text) }, t("c.copy"))));

// Anleitung mit Kopplungscode (ein oder mehrere Stellplätze an einem Pi)
function enrollBox(r) {
  const c = r.install.commands;
  return el("div", { class: "alert-box info" },
    el("h3", {}, t("ag.setup_title")),
    el("p", {}, t("ag.code_label")), el("p", { class: "enroll-code mono" }, r.code),
    el("p", { class: "small muted" }, t("ag.code_expires", { t: fmtDateTime(r.expires_at) })),
    r.station_ids && r.station_ids.length > 1 ? el("p", {}, t("ag.multi_code", { n: r.station_ids.length })) : null,
    r.install.tls ? el("p", { class: "small" }, t("ag.tls_hint"), " ", el("code", { class: "mono" }, r.install.tls.fingerprint)) : null,
    el("ol", { class: "steps" },
      el("li", {}, t("ag.step_os")),
      el("li", {}, t("ag.step_hw")),
      c.fetch_cert ? el("li", {}, cmdRow(t("ag.step_cert"), c.fetch_cert)) : null,
      el("li", {}, cmdRow(t("ag.step_download"), c.download)),
      el("li", {}, cmdRow(t("ag.step_verify"), c.verify)),
      el("li", {}, cmdRow(t("ag.step_install"), c.install)),
      el("li", {}, t("ag.step_done"))),
    el("details", {}, el("summary", {}, t("ag.oneliner")), el("p", { class: "small muted" }, t("ag.oneliner_hint")),
      cmdRow("", c.oneliner)),
    el("p", { class: "small muted" }, t("ag.simulator_hint"), " ", el("code", { class: "mono" }, "--source simulator")));
}

// Auswahl für Port/Leser eines Stellplatzes am Pi: automatisch oder fest
function assignSelect(d, key, options, label) {
  const current = d[`assigned_${key}`] || "";
  const sel = el("select", { "aria-label": label },
    el("option", { value: "" }, t("ag.auto")),
    options.map((o) => el("option", { value: o.value, selected: o.value === current }, o.label)));
  if (current && !options.some((o) => o.value === current)) sel.append(el("option", { value: current, selected: true }, `${current} (${t("ag.missing")})`));
  sel.addEventListener("change", async () => {
    try { await put(`/api/v1/devices/${d.id}/assign`, { [key]: sel.value }); toast(t("ag.assigned")); }
    catch (e) { toast(describeError(e), "error"); }
  });
  return sel;
}

const shortPort = (p) => p.replace("/dev/serial/by-id/", "").replace(/-if\d+(-port\d+)?$/, "");

export function viewDevices() {
  const box = el("div", {}, el("p", {}, t("c.loading")));
  const reveal = el("div");
  let stations = [];
  const load = async () => {
    try {
      const [{ devices, latest_version }, st] = await Promise.all([get("/api/v1/devices"), get("/api/v1/stations")]);
      stations = st.stations;
      const active = devices.filter((d) => !d.revoked_at);
      const online = active.filter((d) => d.online).length;
      const groups = new Map();
      for (const d of active) {
        const g = groups.get(d.gateway_id) || [];
        g.push(d);
        groups.set(d.gateway_id, g);
      }
      const command = async (d, cmd) => {
        try { await post(`/api/v1/stations/${d.station_id}/devices/${d.id}/command`, { command: cmd }); toast(t("ag.queued")); }
        catch (e) { toast(describeError(e), "error"); }
      };
      clear(box, el("p", {}, t("ag.fleet_summary", { online, total: active.length, v: latest_version })),
        active.length ? [...groups.values()].map((list) => {
          const head = list[0];
          const hw = list.find((d) => d.hw)?.hw;
          const ports = (hw?.ports || []).map((p) => ({ value: p.path, label: `${shortPort(p.path)} · ${p.kind}${p.firmware ? " · " + p.firmware : ""}` }));
          const readers = (hw?.readers || []).map((r) => ({ value: r.id, label: `${r.name} (${r.kind.toUpperCase()})` }));
          return el("section", { class: "card gateway-card" },
            el("h2", {}, icon("plug"), " ", head.hostname || head.name, " ", deviceStatus(head)),
            el("p", { class: "small muted" }, [t("ag.version"), ": ", head.agent_version || "–", " · ", healthSummary(head),
              hw ? [" · ", t("ag.hw_summary", { p: hw.ports.length, r: hw.readers.length }), " · ", t("ag.camera"), ": ", hw.camera || "–",
                " · ", t("ag.kiosk"), ": ", hw.kiosk ? t("c.yes") : t("c.no")] : null]),
            head.update_available ? el("p", {}, el("span", { class: "badge warn" }, t("ag.update_avail", { v: latest_version }))) : null,
            el("div", { class: "table-wrap" }, el("table", {},
              el("thead", {}, el("tr", {}, [t("ev.station"), t("ag.port"), t("ag.reader"), t("c.status"), t("ag.last_contact"), can("admin") ? t("c.actions") : null]
                .filter(Boolean).map((h) => el("th", { scope: "col" }, h)))),
              el("tbody", {}, list.map((d) => el("tr", {},
                el("td", {}, can("admin") ? el("a", { href: `#/stations/${d.station_id}/settings` }, d.station_name) : d.station_name),
                el("td", {}, d.managed && can("admin") && ports.length ? assignSelect(d, "port", ports, t("ag.port"))
                  : el("span", { class: "mono small" }, d.hw?.port ? shortPort(d.hw.port) : "–"),
                  d.hw && d.managed ? el("div", { class: "small muted" }, d.source === "simulator" ? t("ag.sim_port")
                    : d.hw.port ? t("ag.in_use", { v: shortPort(d.hw.port) }) : t("ag.no_port")) : null),
                el("td", {}, d.managed && can("admin") && readers.length ? assignSelect(d, "reader", readers, t("ag.reader"))
                  : el("span", { class: "small" }, d.hw?.reader || "–")),
                el("td", {}, deviceStatus(d)),
                el("td", { class: "small" }, fmtDateTime(d.last_heartbeat_at || d.last_seen_at)),
                can("admin") ? el("td", {}, d.managed ? el("button", { class: "btn small", type: "button", onclick: () => command(d, "identify") }, t("ag.identify")) : "–") : null))))));
        }) : el("p", { class: "card muted" }, t("ag.none")));
    } catch (e) { clear(box, errorCard(e)); }
  };
  every(15000, load);

  // Ein Pi für mehrere Stellplätze
  const multi = can("admin") ? el("details", { class: "card" }, el("summary", {}, "+ ", t("ag.multi_setup")),
    el("p", { class: "muted" }, t("ag.multi_hint"))) : null;
  if (multi) {
    const form = el("form", {});
    multi.append(form);
    multi.addEventListener("toggle", () => {
      if (!multi.open) return;
      const boxes = stations.map((s) => ({ s, box: el("input", { type: "checkbox", value: s.id }) }));
      clear(form, el("fieldset", { class: "field" }, el("legend", {}, t("ag.multi_pick")),
        el("div", { class: "grid cols-2 checks" }, boxes.map(({ s, box }) => el("label", { class: "check" }, box, el("span", {}, s.name))))),
      el("button", { class: "btn primary", type: "submit" }, t("ag.multi_create")));
      form.onsubmit = async (ev) => {
        ev.preventDefault();
        const ids = boxes.filter((x) => x.box.checked).map((x) => x.s.id);
        if (!ids.length) return toast(t("ag.multi_none"), "error");
        try { clear(reveal, enrollBox(await post("/api/v1/stations/enrollments", { name: "Pi-Gateway", station_ids: ids }))); load(); }
        catch (e) { toast(describeError(e), "error"); }
      };
    });
  }
  return el("div", { class: "page-stack" }, el("p", { class: "muted" }, t("ag.fleet_hint")), multi, reveal, box);
}

// ---------------------------------------------------------------------- NFC-Lesegeräte
export function viewReaders() {
  const box = el("div", { class: "page-stack" }, el("p", {}, t("c.loading")));
  const learnBox = el("section", { class: "card" });
  const load = async () => {
    try {
      const { gateways, learn } = await get("/api/v1/readers");
      renderLearn(learn);
      const withReaders = gateways.filter((g) => g.readers.length || g.stalls.some((s) => s.reader));
      clear(box, withReaders.length ? withReaders.map((g) => el("section", { class: "card" },
        el("h2", {}, icon("card"), " ", g.hostname || t("ag.gateway"), " ",
          el("span", { class: `badge ${g.online ? "ok" : "warn"}` }, g.online ? t("ag.online") : t("ag.offline"))),
        el("div", { class: "table-wrap" }, el("table", {},
          el("thead", {}, el("tr", {}, [t("rd.reader"), t("rd.kind"), t("ev.station"), t("sx.taps"), t("sx.tap_success"), t("sx.last_tap")]
            .map((h) => el("th", { scope: "col" }, h)))),
          el("tbody", {}, g.readers.map((r) => el("tr", {},
            el("td", {}, r.name, el("div", { class: "small muted mono" }, r.id)),
            el("td", {}, t("rd.kind_" + (r.kind || "pn532"))),
            el("td", {}, r.station_name || el("span", { class: "badge warn" }, t("rd.unassigned"))),
            el("td", {}, String(r.taps)),
            el("td", {}, r.success === null ? "–" : `${Math.round(r.success * 100)} %`),
            el("td", { class: "small" }, fmtDateTime(r.last_at))))))),
        el("p", { class: "small muted" }, t("rd.assign_hint"), " ", el("a", { href: "#/devices" }, t("nav.devices")))))
        : el("section", { class: "card" }, el("p", { class: "muted" }, t("rd.none"))),
      el("details", { class: "card" }, el("summary", {}, t("rd.supported")),
        el("ul", {}, ["pn532", "hid", "pcsc"].map((k) => el("li", {}, el("strong", {}, t("rd.kind_" + k)), " – ", t("rd.about_" + k))))));
    } catch (e) { clear(box, errorCard(e)); }
  };

  let learnTimer = null;
  function renderLearn(l) {
    const label = el("input", { type: "text", required: true, maxlength: "100", placeholder: t("rd.learn_ph") });
    const form = el("form", { class: "btn-row" }, field(t("pk.label"), label), el("button", { class: "btn primary", type: "submit" }, t("rd.learn_start")));
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try { renderLearn(await post("/api/v1/cards/learn", { label: label.value })); poll(); }
      catch (e) { toast(describeError(e), "error"); }
    });
    let status = null;
    if (l?.state === "waiting") {
      status = el("div", { class: "alert-box info", role: "status" }, el("strong", {}, t("rd.learn_waiting", { label: l.label, s: l.seconds_left })), " ",
        el("button", { class: "btn small", type: "button", onclick: async () => { await del("/api/v1/cards/learn"); renderLearn({ state: "idle" }); } }, t("c.cancel")));
    } else if (l?.state === "learned") {
      status = el("div", { class: "alert-box ok", role: "status" }, "✓ ", t("rd.learn_done", { label: l.card?.label || l.label, st: l.station_name || "" }));
    } else if (l?.state === "known") {
      status = el("div", { class: "alert-box warn", role: "status" }, t("rd.learn_known", { label: l.card?.label || "" }));
    } else if (l?.state === "expired") {
      status = el("div", { class: "alert-box warn", role: "status" }, t("rd.learn_expired"));
    }
    clear(learnBox, el("h2", {}, t("rd.learn_title")), el("p", { class: "muted" }, t("rd.learn_intro")), status, l?.state === "waiting" ? null : form);
  }
  async function poll() {
    if (learnTimer) clearTimeout(learnTimer);
    try {
      const l = await get("/api/v1/cards/learn");
      renderLearn(l);
      if (l.state === "waiting" && document.body.contains(learnBox)) learnTimer = setTimeout(poll, 1500);
      else load(); // Taps/Leser aktualisieren
    } catch (_) { /* nächster Versuch beim Neuladen */ }
  }
  every(30000, load);
  return el("div", { class: "page-stack" }, learnBox, box);
}

// ---------------------------------------------------------------------- Meldungen
export function viewEvents() {
  const openOnly = el("input", { type: "checkbox" });
  const shadow = el("input", { type: "checkbox" });
  const box = el("div", { class: "card" }, t("c.loading"));
  const load = async () => {
    try {
      const q = new URLSearchParams({ limit: "200", open_only: String(openOnly.checked), include_shadow: String(shadow.checked) });
      const { events } = await get(`/api/v1/events?${q}`);
      clear(box, eventsTable(events, load));
    } catch (e) { clear(box, errorCard(e)); }
  };
  openOnly.addEventListener("change", load);
  shadow.addEventListener("change", load);
  every(15000, load);
  return el("div", {},
    el("div", { class: "btn-row" }, el("label", { class: "check" }, openOnly, el("span", {}, t("ev.open_only"))),
      el("label", { class: "check" }, shadow, el("span", {}, t("ev.show_shadow")))),
    el("p", { class: "small muted" }, t("st.disclaimer")), box);
}

// Druckansicht für den QR-Aufkleber am Stellplatz.
function printSticker(name, qrDataUrl) {
  const sheet = el("div", { class: "print-sticker" },
    el("p", { class: "print-sticker__title" }, name),
    el("img", { src: qrDataUrl, alt: "", width: "260", height: "260" }),
    el("p", {}, t("sv.sticker_text")), el("p", { class: "small" }, "Smart Bicycle Box"));
  document.body.append(sheet);
  document.body.classList.add("printing");
  const done = () => { sheet.remove(); document.body.classList.remove("printing"); window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  window.print();
  setTimeout(done, 1000);
}
