// Betrieb: Übersicht, Stellplätze, Live-Ansicht, Einstellungen, Meldungen.
// Eine Station ist genau ein vorne offener Stellplatz.
import { get, post, patch, del, describeError } from "./api.js";
import { getLang, t } from "./i18n.js";
import { el, clear, field, toast, confirmDialog, fmtDateTime, fmtTime, copyText, icon, effectiveState, reasonText,
  stallStatus, stallBadge, fmtAge } from "./ui.js";
import { state, can, every, go } from "./state.js";

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
      live.simulated_data ? el("span", { class: "badge sim" }, t("st.sim")) : null));
}

// ---------------------------------------------------------------------- Übersicht
export function viewOverview() {
  const kpis = el("div", { class: "grid cols-4" });
  const cards = el("div", { class: "grid cols-3" });
  const node = el("div", {}, kpis, el("h2", { class: "visually-hidden" }, t("nav.stations")), cards);
  every(5000, async () => {
    try {
      const [{ stations }, { events }] = await Promise.all([get("/api/v1/stations"), get("/api/v1/events?open_only=true&limit=100")]);
      const count = (st) => stations.filter((s) => (s.live?.state || "unknown") === st).length;
      clear(kpis, kpi(stations.length, t("ov.stations")), kpi(count("free"), t("ov.state_free")),
        kpi(count("unknown"), t("ov.state_unknown")), kpi(events.length, t("ov.alerts")));
      clear(cards, stations.length ? stations.map(stallCard) : el("div", { class: "card" }, el("p", {}, t("ov.empty")),
        can("admin") ? el("a", { class: "btn primary", href: "#/stations/new" }, t("ov.create")) : null));
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
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const st = await post("/api/v1/stations", { name: name.value, location: loc.value });
      go(`/stations/${st.id}/settings`);
    } catch (e) { clear(err, errorCard(e)); }
  });
  return form;
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
        el("td", {}, t("ev.k_" + e.kind), e.simulated ? [" ", el("span", { class: "badge sim" }, t("c.simulated"))] : null),
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
    el("div", {}, el("section", { "aria-labelledby": "status-h" }, el("h2", { id: "status-h", class: "visually-hidden" }, t("st.current")), statusBox, live), measure, usage),
    el("div", {}, warnings, ai, recent));

  const render = () => {
    if (!last) return;
    const stale = last.stale_after_s || 30;
    const connLost = Date.now() - lastOk > stale * 1000;
    const { state: st, reason } = effectiveState(last, connLost);
    clear(statusBox, stallStatus(last, { connLost, simulated: last.simulated_data }));
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
          can("operator") && a.id ? el("div", { class: "sbb-alert__actions" }, el("button", { class: "btn small", type: "button", onclick: async () => {
            try { await post(`/api/v1/events/${a.id}/ack`); await poll(); loadRecent(); warnings.querySelector("h2")?.focus(); } catch (e) { toast(describeError(e), "error"); }
          } }, t("ev.ack_btn"))) : null));
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
      clear(node, general(st), devicesCard(st), displayCard(st), dangerCard(st));
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

    const cmdRow = (label, text) => el("div", { class: "field" }, el("div", { class: "small muted" }, label),
      el("div", { class: "btn-row" }, el("code", { class: "secret-box mono small cmd" }, text),
        el("button", { class: "btn small", type: "button", onclick: () => copyText(text) }, t("c.copy"))));

    const setup = el("button", { class: "btn primary", type: "button", onclick: async () => {
      try {
        const r = await post(`/api/v1/stations/${id}/enrollments`, { name: "Pi-Gateway" });
        const c = r.install.commands;
        clear(reveal, el("div", { class: "alert-box info" },
          el("h3", {}, t("ag.setup_title")),
          el("p", {}, t("ag.code_label")), el("p", { class: "enroll-code mono" }, r.code),
          el("p", { class: "small muted" }, t("ag.code_expires", { t: fmtDateTime(r.expires_at) })),
          r.install.tls ? el("p", { class: "small" }, t("ag.tls_hint"), " ", el("code", { class: "mono" }, r.install.tls.fingerprint)) : null,
          el("ol", { class: "steps" },
            el("li", {}, t("ag.step_os")),
            c.fetch_cert ? el("li", {}, cmdRow(t("ag.step_cert"), c.fetch_cert)) : null,
            el("li", {}, cmdRow(t("ag.step_download"), c.download)),
            el("li", {}, cmdRow(t("ag.step_verify"), c.verify)),
            el("li", {}, cmdRow(t("ag.step_install"), c.install)),
            el("li", {}, t("ag.step_done"))),
          el("details", {}, el("summary", {}, t("ag.oneliner")), el("p", { class: "small muted" }, t("ag.oneliner_hint")),
            cmdRow("", c.oneliner)),
          el("p", { class: "small muted" }, t("ag.simulator_hint"), " ", el("code", { class: "mono" }, "--source simulator"))));
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

export function viewDevices() {
  const box = el("div", { class: "card" }, t("c.loading"));
  const load = async () => {
    try {
      const { devices, latest_version } = await get("/api/v1/devices");
      const active = devices.filter((d) => !d.revoked_at);
      const online = active.filter((d) => d.online).length;
      clear(box, el("p", {}, t("ag.fleet_summary", { online, total: active.length, v: latest_version })),
        active.length ? el("div", { class: "table-wrap" }, el("table", {},
          el("thead", {}, el("tr", {}, [t("ev.station"), t("ss.device_name"), t("c.status"), t("ag.version"), t("ag.health"), t("ag.last_contact")]
            .map((h) => el("th", { scope: "col" }, h)))),
          el("tbody", {}, active.map((d) => el("tr", {},
            el("td", {}, can("admin") ? el("a", { href: `#/stations/${d.station_id}/settings` }, d.station_name) : d.station_name),
            el("td", {}, d.name, d.hostname ? el("div", { class: "small muted mono" }, d.hostname) : null),
            el("td", {}, deviceStatus(d)),
            el("td", {}, d.agent_version || "–", d.update_available ? el("div", {}, el("span", { class: "badge warn" }, t("ag.update_avail", { v: latest_version }))) : null),
            el("td", { class: "small" }, healthSummary(d)),
            el("td", { class: "small" }, fmtDateTime(d.last_heartbeat_at || d.last_seen_at)))))))
          : el("p", { class: "muted" }, t("ag.none")));
    } catch (e) { clear(box, errorCard(e)); }
  };
  every(15000, load);
  return el("div", {}, el("p", { class: "muted" }, t("ag.fleet_hint")), box);
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
