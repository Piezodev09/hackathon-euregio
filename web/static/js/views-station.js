// Operations: overview, stations, live view, settings, events, gateways.
import { get, post, patch, del, describeError } from "./api.js";
import { getLang, t } from "./i18n.js";
import { el, clear, field, toast, confirmDialog, fmtDateTime, fmtTime, copyText, slotSymbol } from "./ui.js";
import { state, can, every, go } from "./state.js";

let devicesTimer = null;
const errorCard = (e) => el("div", { class: "alert-box error", role: "alert" }, describeError(e));

function kpi(value, label) {
  return el("div", { class: "card kpi" }, el("span", { class: "value" }, String(value)), el("span", { class: "label" }, label));
}

function stationCard(s) {
  const live = s.live || {};
  return el("article", { class: "card" },
    el("h3", {}, s.name),
    s.location ? el("p", { class: "muted small" }, s.location) : null,
    el("p", { class: "kpi" }, el("span", { class: "value" }, `${live.free_count ?? "–"}`),
      el("span", { class: "label" }, t("ov.free_of", { f: live.free_count ?? "–", t: live.total ?? "–" }))),
    el("div", { class: "btn-row" },
      live.alerts ? el("span", { class: "badge warn" }, `⚠ ${live.alerts} ${t("st.alert")}`) : null,
      live.known_count < live.total ? el("span", { class: "badge" }, `? ${live.total - live.known_count} ${t("st.unknown")}`) : null,
      live.simulated_data ? el("span", { class: "badge sim" }, t("c.simulated")) : null),
    el("p", {}, el("a", { class: "btn small", href: `#/stations/${s.id}` }, t("ov.open"))));
}

// ---------------------------------------------------------------------- overview
export function viewOverview() {
  const kpis = el("div", { class: "grid cols-4" });
  const cards = el("div", { class: "grid cols-3" });
  const node = el("div", {}, kpis, el("h2", { class: "visually-hidden" }, t("nav.stations")), el("div", { style: null }, cards));
  every(5000, async () => {
    try {
      const [{ stations }, { events }] = await Promise.all([get("/api/v1/stations"), get("/api/v1/events?open_only=true&limit=100")]);
      const free = stations.reduce((a, s) => a + (s.live?.free_count || 0), 0);
      const known = stations.reduce((a, s) => a + (s.live?.known_count || 0), 0);
      const total = stations.reduce((a, s) => a + (s.live?.total || 0), 0);
      clear(kpis, kpi(free, t("ov.free")), kpi(stations.length, t("ov.stations")), kpi(events.length, t("ov.alerts")), kpi(`${known}/${total}`, t("ov.known")));
      clear(cards, stations.length ? stations.map(stationCard) : el("div", { class: "card" }, el("p", {}, t("ov.empty")),
        can("admin") ? el("a", { class: "btn primary", href: "#/stations/new" }, t("ov.create")) : null));
    } catch (e) { clear(cards, errorCard(e)); }
  });
  return node;
}

export function viewStations() {
  const body = el("div", {}, el("p", {}, t("c.loading")));
  get("/api/v1/stations").then(({ stations }) => {
    clear(body, stations.length ? el("div", { class: "card table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, [t("ss.name"), t("ss.location"), t("ov.free"), t("ss.display"), t("c.actions")].map((h) => el("th", { scope: "col" }, h)))),
      el("tbody", {}, stations.map((s) => el("tr", {},
        el("td", {}, el("a", { href: `#/stations/${s.id}` }, s.name)), el("td", {}, s.location || "–"),
        el("td", {}, t("ov.free_of", { f: s.live.free_count, t: s.live.total })),
        el("td", {}, s.display_enabled ? el("span", { class: "badge ok" }, t("ss.active")) : "–"),
        el("td", {}, el("div", { class: "btn-row" }, el("a", { class: "btn small", href: `#/stations/${s.id}` }, t("ov.open")),
          can("admin") ? el("a", { class: "btn small", href: `#/stations/${s.id}/settings` }, t("st.settings")) : null))))))) :
      el("div", { class: "card" }, el("p", {}, t("ov.empty"))));
  }).catch((e) => clear(body, errorCard(e)));
  return body;
}

export function viewNewStation() {
  const plan = state.me.tenant.plan;
  const err = el("div", { role: "alert" });
  const name = el("input", { type: "text", name: "name", required: true, maxlength: "100", autofocus: true });
  const loc = el("input", { type: "text", name: "location", maxlength: "200" });
  const count = el("input", { type: "number", name: "count", min: "1", max: String(plan.max_slots_per_station), value: String(Math.min(3, plan.max_slots_per_station)), required: true });
  const form = el("form", { class: "card" }, err, field(t("ss.name"), name), field(t("ss.location"), loc),
    field(t("ss.slots_count"), count, `max. ${plan.max_slots_per_station}`),
    el("button", { class: "btn primary", type: "submit" }, t("ss.create")));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const n = Math.max(1, Math.min(plan.max_slots_per_station, parseInt(count.value, 10) || 1));
    const keys = Array.from({ length: n }, (_, i) => (i < 26 ? String.fromCharCode(65 + i) : "P" + (i + 1)));
    const slotWord = t("ev.slot");
    try {
      const st = await post("/api/v1/stations", { name: name.value, location: loc.value, slots: keys.map((k) => ({ key: k, label: `${slotWord} ${k}` })) });
      go(`/stations/${st.id}/settings`);
    } catch (e) { clear(err, errorCard(e)); }
  });
  return form;
}

// ---------------------------------------------------------------------- live view
export function slotList(status, { connLost = false } = {}) {
  const slots = status.slots.map((s) => (connLost ? { ...s, state: "unknown", unknown_reason: "connection" } : s));
  const rec = connLost ? null : status.recommendation;
  return el("ul", { class: "slots" }, slots.map((s) => {
    const isRec = s.slot_id === rec && s.state === "free";
    return el("li", { class: `slot ${s.state}${isRec ? " recommended" : ""}${s.alert ? " alerting" : ""}` },
      el("span", { class: "name" }, s.label),
      el("span", { class: "state" }, el("span", { class: "sym", "aria-hidden": "true" }, slotSymbol(s.state)), t("st." + s.state)),
      s.state === "unknown" && s.unknown_reason ? el("span", { class: "small" }, t("st.r_" + s.unknown_reason)) : null,
      isRec ? el("span", { class: "badge" }, "→ " + t("st.recommended")) : null,
      s.alert ? el("span", { class: "badge warn" }, "⚠ " + t("st.alert")) : null,
      el("span", { class: "small" }, fmtTime(s.last_update)));
  }));
}

export function heatmap(summary, slots) {
  const first = summary.buckets.findIndex((b) => b.avg_occupied_slots !== null);
  const buckets = first < 0 ? [] : summary.buckets.slice(first);
  if (!buckets.length) return el("p", { class: "muted" }, t("st.usage_none"));
  const hourOf = (iso) => String(new Date(iso).getHours()).padStart(2, "0");
  const top = buckets.filter((b) => b.avg_occupied_slots !== null).reduce((a, b) => (b.avg_occupied_slots > a.avg_occupied_slots ? b : a));
  return el("div", {},
    el("p", {}, t("st.usage_text", { h: hourOf(top.hour_start), v: top.avg_occupied_slots.toLocaleString(getLang()) })),
    el("div", { class: "table-wrap", tabindex: "0", role: "region", "aria-label": t("st.usage") }, el("table", { class: "heatmap" },
      el("caption", { class: "visually-hidden" }, t("st.usage")),
      el("thead", {}, el("tr", {}, el("th", { scope: "col" }, t("st.hour")), slots.map((s) => el("th", { scope: "col" }, s.label)))),
      el("tbody", {}, buckets.map((b) => el("tr", {}, el("th", { scope: "row" }, hourOf(b.hour_start)),
        slots.map((s) => {
          const v = b.occupancy[s.slot_id];
          if (v === null || v === undefined) return el("td", { class: "nodata" }, "–");
          return el("td", { class: `lvl${Math.min(4, Math.floor(v * 5))}` }, `${Math.round(v * 100)} %`);
        })))))),
    el("p", { class: summary.contains_simulated ? "badge sim" : "small muted" },
      summary.contains_simulated ? t("st.src_sim") : summary.contains_live ? t("st.src_live") : ""));
}

// Typical week: rows = weekdays, columns = hours (local time of the station). Planning data.
export function weekHeatmap(w) {
  if (!w.matrix.some((row) => row.some((v) => v !== null))) return el("p", { class: "muted" }, t("st.usage_none"));
  const days = Array.from({ length: 7 }, (_, d) =>
    new Date(Date.UTC(2024, 0, 1 + d)).toLocaleDateString(getLang(), { weekday: "short", timeZone: "UTC" }));
  const pct = (v) => `${Math.round(v * 100)} %`;
  const summary = w.peak ? t("st.week_text", { d: new Date(Date.UTC(2024, 0, 1 + w.peak.weekday)).toLocaleDateString(getLang(),
    { weekday: "long", timeZone: "UTC" }), h: String(w.peak.hour).padStart(2, "0"), v: pct(w.peak.share), a: pct(w.average ?? 0) }) : "";
  return el("div", {},
    el("p", {}, summary),
    el("div", { class: "table-wrap", tabindex: "0", role: "region", "aria-label": t("st.usage_week_title") }, el("table", { class: "heatmap week" },
      el("caption", { class: "visually-hidden" }, t("st.usage_week_title")),
      el("thead", {}, el("tr", {}, el("th", { scope: "col" }, t("st.day")),
        Array.from({ length: 24 }, (_, h) => el("th", { scope: "col" }, String(h).padStart(2, "0"))))),
      el("tbody", {}, w.matrix.map((row, d) => el("tr", {}, el("th", { scope: "row" }, days[d]),
        row.map((v) => v === null ? el("td", { class: "nodata", title: "–" }, "")
          : el("td", { class: `lvl${Math.min(4, Math.floor(v * 5))}`, title: `${days[d]} ${pct(v)}` },
            el("span", { class: "visually-hidden" }, pct(v))))))))),
    el("p", { class: "small muted" }, t("st.week_note", { tz: w.timezone }),
      w.contains_simulated ? el("span", { class: "badge sim" }, " " + t("st.src_sim")) : null));
}

function eventsTable(events, reload, { showStation = true } = {}) {
  if (!events.length) return el("p", { class: "muted" }, t("ev.none"));
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, [t("ev.time"), showStation ? t("ev.station") : null, t("ev.slot"), t("ev.kind"), t("ev.detector"), t("ev.ack")]
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
        el("td", {}, fmtDateTime(e.occurred_at)), showStation ? el("td", {}, e.station_name) : null, el("td", {}, e.slot_id),
        el("td", {}, t("ev.k_" + e.kind), e.simulated ? el("span", { class: "badge sim" }, " " + t("c.simulated")) : null),
        el("td", {}, e.detector ? t("ev.d_" + e.detector) + (e.severity === "shadow" ? " " + t("ev.shadow") : "") : "–"),
        el("td", {}, ack));
    }))));
}

export function viewStation(id, setTitle) {
  const summary = el("div", { class: "card" });
  const alerts = el("div", { role: "alert", "aria-live": "assertive" });
  const slotsBox = el("div", { class: "card" });
  const ai = el("div", { class: "card" });
  const usage = el("div", { class: "card" });
  const recent = el("div", { class: "card" });
  let lastOk = 0;
  let last = null;
  let alertText = "";

  const node = el("div", {}, summary, alerts, el("section", { "aria-label": t("st.slots") }, slotsBox),
    usage, el("div", { class: "grid cols-2" }, ai, recent));

  const render = () => {
    if (!last) return;
    const connLost = Date.now() - lastOk > (last.stale_after_s || 30) * 1000;
    const free = connLost ? 0 : last.free_count;
    const rec = !connLost && last.recommendation ? last.slots.find((s) => s.slot_id === last.recommendation) : null;
    clear(summary,
      el("p", { class: "kpi" }, el("span", { class: "value" }, t("st.free_of", { f: free, t: last.total }))),
      el("p", { class: "big" }, rec ? t("st.rec", { s: rec.label }) : t("st.rec_none")),
      el("div", { class: "btn-row" },
        el("span", { class: connLost ? "badge err" : "badge ok", role: "status" }, connLost ? "⚠ " + t("st.conn_lost") : "● " + t("st.conn_ok")),
        el("span", { class: "small muted" }, t("st.updated", { t: fmtTime(last.server_time) })),
        last.simulated_data ? el("span", { class: "badge sim" }, t("st.sim")) : null));
    clear(slotsBox, el("h2", {}, t("st.slots")), slotList(last, { connLost }));
    const active = last.slots.filter((s) => s.alert);
    const text = active.map((s) => t("st.alert_text", { slot: s.label, t: fmtTime(s.alert.occurred_at) })).join("|");
    if (text !== alertText) {
      alertText = text;
      clear(alerts, active.map((s) => el("div", { class: "alert-box warn" }, "⚠ ",
        t("st.alert_text", { slot: s.label, t: fmtTime(s.alert.occurred_at) }), s.alert.simulated ? ` [${t("c.simulated")}]` : "",
        can("operator") && s.alert.id ? el("button", { class: "btn small", type: "button", onclick: async () => {
          try { await post(`/api/v1/events/${s.alert.id}/ack`); poll(); loadRecent(); } catch (e) { toast(describeError(e), "error"); }
        } }, " ", t("ev.ack_btn")) : null)));
    }
    const a = last.ai;
    clear(ai, el("h2", {}, t("st.ai")),
      el("p", {}, a.visible_detector === "ml" ? t("st.ai_ml") : t("st.ai_rule")),
      el("p", { class: "small" }, !a.plan_allows_ml ? t("st.ai_plan") : a.model_available ? t("st.ai_ok") : t("st.ai_off")),
      a.model_trained_on_simulated_data ? el("p", { class: "badge sim" }, t("st.ai_sim")) : null,
      el("p", { class: "small muted" }, t("st.disclaimer")));
  };

  const poll = async () => {
    try {
      last = await get(`/api/v1/stations/${id}/status`);
      lastOk = Date.now();
      setTitle(last.display_name);
    } catch (e) {
      if (!last) clear(summary, errorCard(e));
    }
    render();
  };
  let usageView = "day";
  const usageToggle = () => el("div", { class: "btn-row", role: "group", "aria-label": t("st.usage") },
    ["day", "week"].map((v) => el("button", { class: "btn small", type: "button", "aria-pressed": String(usageView === v),
      onclick: () => { usageView = v; loadUsage(); } }, t(v === "day" ? "st.usage_day" : "st.usage_week"))));
  const loadUsage = async () => {
    try {
      if (usageView === "week") {
        const w = await get(`/api/v1/stations/${id}/occupancy/week?days=7`);
        clear(usage, el("h2", {}, t("st.usage_week_title")), usageToggle(), weekHeatmap(w));
      } else {
        const s = await get(`/api/v1/stations/${id}/occupancy?hours=24`);
        if (last) clear(usage, el("h2", {}, t("st.usage")), usageToggle(), heatmap(s, last.slots));
      }
    } catch (_) {}
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

// ---------------------------------------------------------------------- settings
export function viewStationSettings(id, setTitle) {
  const node = el("div", {}, el("p", {}, t("c.loading")));
  const load = async () => {
    try {
      const st = await get(`/api/v1/stations/${id}`);
      setTitle(`${st.name} – ${t("st.settings")}`);
      clear(node, general(st), slotsCard(st), devicesCard(st), displayCard(st), dangerCard(st));
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

  function slotsCard(st) {
    const key = el("input", { type: "text", required: true, maxlength: "16", pattern: "[A-Za-z0-9_\\-]{1,16}" });
    const label = el("input", { type: "text", required: true, maxlength: "100" });
    const add = el("form", { class: "btn-row" }, field(t("ss.key"), key, t("ss.key_hint")), field(t("ss.label"), label),
      el("button", { class: "btn", type: "submit" }, t("ss.add_slot")));
    add.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try { await post(`/api/v1/stations/${id}/slots`, { key: key.value, label: label.value }); load(); } catch (e) { toast(describeError(e), "error"); }
    });
    return el("section", { class: "card" }, el("h2", {}, t("ss.slots")),
      el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, [t("ss.position"), t("ss.key"), t("ss.label"), t("c.actions")].map((h) => el("th", { scope: "col" }, h)))),
        el("tbody", {}, st.slots.map((s) => el("tr", {}, el("td", {}, String(s.position)), el("td", { class: "mono" }, s.key), el("td", {}, s.label),
          el("td", {}, el("button", { class: "btn small danger", type: "button", onclick: async () => {
            if (!(await confirmDialog(`${s.label}: ${t("c.delete")}?`, { danger: true }))) return;
            try { await del(`/api/v1/stations/${id}/slots/${s.id}`); load(); } catch (e) { toast(describeError(e), "error"); }
          } }, t("c.delete")))))))), add);
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
          el("ol", { class: "steps" },
            el("li", {}, t("ag.step_os")),
            el("li", {}, cmdRow(t("ag.step_download"), c.download),
              r.install.ca_fingerprint ? el("p", { class: "small muted" }, t("ag.ca_download_hint")) : null),
            el("li", {}, cmdRow(t("ag.step_verify"), c.verify)),
            el("li", {}, cmdRow(t("ag.step_install"), c.install)),
            el("li", {}, t("ag.step_done"))),
          r.install.ca_fingerprint ? el("div", { class: "small" }, el("p", {}, t("ag.ca_pinned")),
            el("p", { class: "mono small" }, "SHA-256 ", r.install.ca_fingerprint)) : null,
          c.oneliner ? el("details", {}, el("summary", {}, t("ag.oneliner")), el("p", { class: "small muted" }, t("ag.oneliner_hint")),
            cmdRow("", c.oneliner)) : null,
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

    // manual token (advanced / without install script)
    const name = el("input", { type: "text", required: true, maxlength: "100", value: "Pi-Gateway" });
    const manual = el("form", { class: "btn-row" }, field(t("ss.device_name"), name), el("button", { class: "btn", type: "submit" }, t("ss.add_device")));
    const manualReveal = el("div");
    manual.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        const d = await post(`/api/v1/stations/${id}/devices`, { name: name.value });
        const key = st.slots[0]?.key || "A";
        const cfg = `curl -X POST ${location.origin}/api/v1/measurements \\\n  -H "Authorization: Bearer ${d.token}" -H "Content-Type: application/json" \\\n  -d '{"station_id":"${d.station_id}","slot_id":"${key}","sequence":1,"occupied":true,"vibration_score":0,"sensor_state":"ok"}'`;
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

// ---------------------------------------------------------------------- events
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
