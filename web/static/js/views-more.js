// Erste Schritte, Reservierungen, Öffnungs-/Sperrzeiten, Berichte, Integrationen (API/Webhooks), Benachrichtigungen.
import { get, post, patch, put, del, describeError } from "./api.js";
import { getLang, t } from "./i18n.js";
import { el, clear, field, toast, confirmDialog, fmtDateTime, fmtCents, copyText, icon, remainingMin } from "./ui.js";
import { state, can, every, go } from "./state.js";

const errorCard = (e) => el("div", { class: "alert-box error", role: "alert" }, describeError(e));
const th = (...hs) => el("thead", {}, el("tr", {}, hs.filter(Boolean).map((h) => el("th", { scope: "col" }, h))));
const planHas = (f) => !!state.me?.tenant?.plan?.[f];
const upsell = (f) => el("div", { class: "alert-box info upsell" }, icon("info"),
  el("div", {}, el("p", {}, el("strong", {}, t("up.title", { f: t("feat." + f) }))), el("p", { class: "small" }, t("up.text")),
    el("a", { class: "btn small primary", href: "#/billing" }, t("up.cta"))));
const today = () => new Date().toLocaleDateString("sv-SE");

// ---------------------------------------------------------------------- Erste Schritte (Übersicht)
export async function createDemoStation(after) {
  try {
    const r = await post("/api/v1/onboarding/demo-station");
    toast(t("ob.demo_created"));
    if (after) after(r); else go(`/stations/${r.id}`);
  } catch (e) { toast(describeError(e), "error"); }
}

export function onboardingCard() {
  const box = el("section", { class: "card onboarding", "data-tour": "onboarding", "aria-labelledby": "ob-h" });
  const load = async () => {
    try {
      const ob = await get("/api/v1/onboarding");
      if (ob.hidden || !can("admin")) { box.hidden = true; return; }
      const pct = Math.round((ob.done / Math.max(1, ob.total)) * 100);
      const bar = el("span");
      bar.style.width = pct + "%";
      const demo = ob.steps.find((s) => s.id === "demo");
      clear(box,
        el("div", { class: "ob-head" },
          el("div", {}, el("h2", { id: "ob-h" }, ob.done >= ob.total ? t("ob.title_done") : t("ob.title")),
            el("p", { class: "muted" }, t("ob.progress", { n: ob.done, m: ob.total }))),
          el("div", { class: "btn-row" },
            el("button", { class: "btn small", type: "button", onclick: () => window.dispatchEvent(new Event("sbb:tour")) }, icon("play"), t("tour.start")),
            el("button", { class: "btn small", type: "button", onclick: async () => {
              try { await post("/api/v1/onboarding/hide", { hidden: true }); box.hidden = true; } catch (e) { toast(describeError(e), "error"); }
            } }, t("ob.hide")))),
        el("div", { class: "meter", role: "img", "aria-label": t("ob.progress", { n: ob.done, m: ob.total }) }, bar),
        !demo.done && ob.demo_available ? el("div", { class: "ob-demo" }, icon("play"),
          el("div", {}, el("p", {}, el("strong", {}, t("ob.demo_title"))), el("p", { class: "small" }, t("ob.demo_text"))),
          el("button", { class: "btn primary", type: "button", onclick: () => createDemoStation() }, t("ob.demo_btn"))) : null,
        el("ol", { class: "ob-steps" }, ob.steps.filter((s) => s.id !== "demo").map((s) => el("li", { class: s.done ? "done" : null },
          icon(s.done ? "ok" : "nodata"),
          el("div", {}, el("a", { href: s.href }, t("ob.s_" + s.id)), s.optional ? el("span", { class: "small muted" }, " · " + t("c.optional")) : null,
            el("p", { class: "small muted" }, t("ob.h_" + s.id)))))));
      box.hidden = false;
    } catch (_) { box.hidden = true; }
  };
  box.reload = load;
  load();
  return box;
}

// ---------------------------------------------------------------------- Reservierungen
function reservationLine(r) {
  const who = r.card_label ? t("res.for_card", { card: r.card_label }) : t("res.hold");
  return [who, r.label ? ` · ${r.label}` : "", r.via === "api" ? ` · ${t("res.via_api")}` : ""].join("");
}

export function reserveForm(stations, cards, onDone, fixedStation) {
  const st = fixedStation ? null : el("select", { required: true }, stations.map((s) => el("option", { value: s.id }, s.name)));
  const mins = el("select", {}, [15, 30, 60, 120, 240].map((m) => el("option", { value: String(m), selected: m === 30 }, t("res.minutes", { n: m }))));
  const card = el("select", {}, el("option", { value: "" }, t("res.no_card")), cards.map((c) => el("option", { value: c.id }, c.label)));
  const label = el("input", { type: "text", maxlength: "200", placeholder: t("res.label_ph") });
  const f = el("form", { class: "res-form" },
    el("div", { class: "grid cols-4" }, st ? field(t("ev.station"), st) : null, field(t("res.duration"), mins),
      field(t("res.card"), card, t("res.card_hint")), field(t("res.label"), label)),
    el("button", { class: "btn primary", type: "submit" }, icon("reserved"), t("res.create")));
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await post(`/api/v1/stations/${fixedStation || st.value}/reservations`, { minutes: parseInt(mins.value, 10),
        card_id: card.value || null, label: label.value });
      toast(t("res.created"));
      onDone();
    } catch (e) { toast(describeError(e), "error"); }
  });
  return f;
}

export function reservationBox(stationId, status, reload) {
  if (!planHas("reservations")) return null;
  const r = status.reservation;
  if (r) {
    return el("div", { class: "session-box res-box" }, icon("reserved"),
      el("div", {},
        el("p", { class: "session-box__title" }, t("res.active_until", { t: new Date(r.until).toLocaleTimeString(getLang(), { hour: "2-digit", minute: "2-digit" }), m: remainingMin(r) })),
        el("p", { class: "small" }, reservationLine(r), status.state !== "reserved" ? ` · ${t("res.not_shown")}` : ""),
        can("operator") ? el("button", { class: "btn small", type: "button", onclick: async () => {
          try { await del(`/api/v1/reservations/${r.id}`); reload(); } catch (e) { toast(describeError(e), "error"); }
        } }, t("res.cancel")) : null));
  }
  if (!can("operator") || status.maintenance || status.closed || status.state === "occupied") return null;
  const holder = el("div");
  const btn = el("button", { class: "btn", type: "button", onclick: async () => {
    const { cards } = await get("/api/v1/cards").catch(() => ({ cards: [] }));
    clear(holder, el("div", { class: "card inner" }, el("h3", {}, t("res.new")), reserveForm([], cards.filter((c) => c.status === "active"), () => { clear(holder, btn); reload(); }, stationId)));
  } }, icon("reserved"), t("res.reserve"));
  return clear(holder, btn);
}

export function viewReservations() {
  const list = el("section", { class: "card" }, t("c.loading"));
  const form = el("section", { class: "card" });
  const load = async () => {
    try {
      const [{ reservations }, { stations }, cardsR] = await Promise.all([get("/api/v1/reservations"), get("/api/v1/stations"),
        get("/api/v1/cards").catch(() => ({ cards: [] }))]);
      const active = reservations.filter((r) => r.status === "active");
      clear(list, el("h2", {}, t("res.list")),
        reservations.length ? el("div", { class: "table-wrap" }, el("table", {},
          th(t("ev.station"), t("res.for"), t("res.until"), t("c.status"), can("operator") ? t("c.actions") : null),
          el("tbody", {}, reservations.map((r) => el("tr", {},
            el("td", {}, r.station_name || "–"), el("td", {}, reservationLine(r)),
            el("td", {}, fmtDateTime(r.until), r.status === "active" ? el("div", { class: "small muted" }, t("res.left", { m: remainingMin(r) })) : null),
            el("td", {}, el("span", { class: r.status === "active" ? "badge res" : "badge" }, t("res.s_" + r.status)),
              r.simulated ? [" ", el("span", { class: "badge sim" }, t("c.simulated"))] : null),
            can("operator") ? el("td", {}, r.status === "active" ? el("button", { class: "btn small", type: "button", onclick: async () => {
              try { await del(`/api/v1/reservations/${r.id}`); load(); } catch (e) { toast(describeError(e), "error"); }
            } }, t("res.cancel")) : "–") : null))))) : el("p", { class: "muted" }, t("res.none")));
      if (can("operator") && !form.dataset.ready) {
        form.dataset.ready = "1";
        clear(form, el("h2", {}, t("res.new")), el("p", { class: "muted" }, t("res.intro")),
          stations.length ? reserveForm(stations, cardsR.cards.filter((c) => c.status === "active"), load) : el("p", {}, t("ov.empty")));
      }
      list.dataset.active = String(active.length);
    } catch (e) { clear(list, errorCard(e)); }
  };
  if (!planHas("reservations")) return upsell("reservations");
  every(10000, load);
  return el("div", { class: "page-stack" }, form, list);
}

// ---------------------------------------------------------------------- Öffnungs- und Sperrzeiten (Stellplatz-Einstellungen)
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

export function hoursCard(st, reload) {
  const always = el("input", { type: "checkbox", checked: !st.hours });
  const rows = DAYS.map((d) => {
    const range = st.hours?.[d]?.[0];
    const open = el("input", { type: "checkbox", checked: st.hours ? !!range : ["mon", "tue", "wed", "thu", "fri"].includes(d) });
    const from = el("input", { type: "time", value: range?.[0] || "07:00", "aria-label": t("hr.from") + " " + t("hr." + d) });
    const to = el("input", { type: "time", value: range?.[1] || "18:00", "aria-label": t("hr.to") + " " + t("hr." + d) });
    return { d, open, from, to, row: el("div", { class: "hours-row" }, el("label", { class: "check" }, open, el("span", {}, t("hr." + d))), from, el("span", {}, "–"), to) };
  });
  const table = el("div", { class: "hours" }, rows.map((r) => r.row));
  const sync = () => { table.hidden = always.checked; };
  always.addEventListener("change", sync);
  sync();
  const save = el("form", {}, el("label", { class: "check" }, always, el("span", {}, t("hr.always"), el("small", { class: "hint" }, t("hr.always_hint")))),
    table, el("button", { class: "btn primary", type: "submit" }, t("c.save")));
  save.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const hours = always.checked ? null : Object.fromEntries(rows.map((r) => [r.d, r.open.checked ? [[r.from.value, r.to.value]] : []]));
    try { await put(`/api/v1/stations/${st.id}/hours`, { hours }); toast(t("c.saved")); reload(); } catch (e) { toast(describeError(e), "error"); }
  });

  const closures = el("div");
  const loadClosures = async () => {
    try {
      const { closures: list } = await get("/api/v1/closures");
      const mine = list.filter((c) => !c.station_id || c.station_id === st.id);
      clear(closures, mine.length ? el("ul", { class: "plain-list" }, mine.map((c) => el("li", { class: "btn-row" },
        el("span", {}, icon("lock"), " ", fmtDateTime(c.starts_at), " – ", fmtDateTime(c.ends_at), c.note ? ` · ${c.note}` : "",
          " ", el("span", { class: "badge" }, c.station_id ? t("hr.this_stall") : t("hr.all_stalls"))),
        el("button", { class: "btn small danger", type: "button", onclick: async () => {
          try { await del(`/api/v1/closures/${c.id}`); loadClosures(); reload(); } catch (e) { toast(describeError(e), "error"); }
        } }, t("c.delete"))))) : el("p", { class: "muted small" }, t("hr.no_closures")));
    } catch (e) { clear(closures, errorCard(e)); }
  };
  loadClosures();
  const scope = el("select", {}, el("option", { value: st.id }, t("hr.this_stall")), el("option", { value: "" }, t("hr.all_stalls")));
  const a = el("input", { type: "datetime-local", required: true });
  const b = el("input", { type: "datetime-local", required: true });
  const note = el("input", { type: "text", maxlength: "200", placeholder: t("hr.note_ph") });
  const cf = el("form", {}, el("div", { class: "grid cols-4" }, field(t("hr.scope"), scope), field(t("hr.from"), a), field(t("hr.to"), b), field(t("hr.note"), note)),
    el("button", { class: "btn", type: "submit" }, icon("lock"), t("hr.add_closure")));
  cf.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await post("/api/v1/closures", { station_id: scope.value || null, starts_at: a.value, ends_at: b.value, note: note.value });
      a.value = b.value = note.value = "";
      toast(t("c.saved"));
      loadClosures();
      reload();
    } catch (e) { toast(describeError(e), "error"); }
  });
  return el("section", { class: "card", id: "hours" }, el("h2", {}, t("hr.title")), el("p", { class: "muted" }, t("hr.intro")), save,
    el("h3", {}, t("hr.closures")), el("p", { class: "muted small" }, t("hr.closures_intro")), closures, cf);
}

// ---------------------------------------------------------------------- Berichte
export function viewReports() {
  if (!planHas("reports")) return upsell("reports");
  let period = "week";
  let day = today();
  const out = el("div", { class: "page-stack" });
  const tabs = el("div", { class: "seg", role: "group", "aria-label": t("rep.period") });
  const date = el("input", { type: "date", value: day, "aria-label": t("rep.date"), onchange: () => { day = date.value || today(); load(); } });
  const renderTabs = () => clear(tabs, ["day", "week", "month"].map((p) => el("button", { type: "button", "aria-pressed": String(p === period),
    onclick: () => { period = p; renderTabs(); load(); } }, t("rep.p_" + p))));
  renderTabs();
  const q = () => `period=${period}&day=${day}`;
  const load = async () => {
    try {
      const r = await get(`/api/v1/reports?${q()}`);
      const tt = r.totals;
      const pct = (v) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)} %`);
      clear(out,
        el("div", { class: "card" }, el("h2", {}, r.title),
          r.contains_simulated ? el("p", {}, el("span", { class: "badge sim" }, t("rep.sim"))) : null,
          el("div", { class: "grid cols-5 kpis" },
            [[pct(tt.occupancy), t("rep.occupancy")], [tt.checkins, t("rep.checkins")], [fmtCents(tt.fees_cents), t("rep.fees")],
              [tt.warnings, t("rep.warnings")], [tt.problems, t("rep.problems")]].map(([v, l]) =>
              el("div", { class: "kpi-tile" }, el("span", { class: "value" }, String(v)), el("span", { class: "label" }, l)))),
          el("div", { class: "btn-row" },
            el("a", { class: "btn", href: `/api/v1/reports?${q()}&format=pdf`, download: "" }, icon("report"), t("rep.pdf")),
            el("a", { class: "btn", href: `/api/v1/reports?${q()}&format=csv`, download: "" }, t("rep.csv")),
            el("button", { class: "btn", type: "button", onclick: async () => {
              try { const s = await post(`/api/v1/reports/send?${q()}`); toast(t("rep.sent", { to: s.to })); } catch (e) { toast(describeError(e), "error"); }
            } }, icon("bell"), t("rep.send")))),
        el("div", { class: "card" }, el("h2", {}, t("rep.per_stall")),
          r.stations.length ? el("div", { class: "table-wrap" }, el("table", {},
            th(t("ev.station"), t("rep.occupancy"), t("rep.coverage"), t("rep.checkins"), t("rep.fees"), t("rep.warnings"), t("rep.problems"), t("rep.reservations")),
            el("tbody", {}, r.stations.map((s) => {
              const bar = el("span");
              bar.style.width = s.occupancy === null ? "0%" : `${Math.round(s.occupancy * 100)}%`;
              return el("tr", {},
                el("td", {}, s.name, s.simulated ? [" ", el("span", { class: "badge sim" }, t("c.simulated"))] : null),
                el("td", {}, el("div", { class: "bar-cell" }, el("span", { class: "meter", "aria-hidden": "true" }, bar), pct(s.occupancy))),
                el("td", {}, pct(s.data_coverage)), el("td", {}, String(s.checkins)), el("td", { class: "mono" }, fmtCents(s.fees_cents)),
                el("td", {}, String(s.warnings)), el("td", {}, String(s.problems)), el("td", {}, String(s.reservations)));
            })))) : el("p", { class: "muted" }, t("ov.empty")),
          el("p", { class: "small muted" }, t("rep.explain"))),
        el("p", { class: "small muted" }, t("rep.mail_hint"), " ", el("a", { href: "#/security" }, t("nav.security"))));
    } catch (e) { clear(out, errorCard(e)); }
  };
  load();
  return el("div", {}, el("div", { class: "btn-row toolbar" }, tabs, el("label", { class: "inline-field" }, el("span", {}, t("rep.date")), date)), out);
}

// ---------------------------------------------------------------------- Integrationen (API-Schlüssel, Webhooks)
export function viewIntegrations() {
  if (!can("admin")) return el("div", { class: "alert-box error" }, t("err.forbidden"));
  const node = el("div", { class: "page-stack" }, el("p", {}, t("c.loading")));
  const reveal = (title, secret, hint) => el("div", { class: "alert-box warn", role: "status" }, el("h3", {}, title), el("p", {}, hint),
    el("div", { class: "btn-row" }, el("code", { class: "secret-box mono small" }, secret),
      el("button", { class: "btn small", type: "button", onclick: () => copyText(secret) }, t("c.copy"))));

  const load = async (flash) => {
    try {
      const r = await get("/api/v1/integrations");
      if (!r.enabled) return clear(node, upsell("integrations"));
      const base = r.base_url;
      // API-Schlüssel
      const name = el("input", { type: "text", required: true, maxlength: "100", placeholder: t("int.key_name_ph") });
      const sRead = el("input", { type: "checkbox", checked: true });
      const sRes = el("input", { type: "checkbox" });
      const kf = el("form", {}, el("div", { class: "grid cols-2" }, field(t("int.key_name"), name),
        el("fieldset", { class: "field" }, el("legend", {}, t("int.scopes")),
          el("label", { class: "check" }, sRead, el("span", {}, t("int.scope_read"))),
          el("label", { class: "check" }, sRes, el("span", {}, t("int.scope_reservations"))))),
        el("button", { class: "btn primary", type: "submit" }, t("int.key_create")));
      kf.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const scopes = [sRead.checked ? "read" : null, sRes.checked ? "reservations" : null].filter(Boolean);
        try {
          const k = await post("/api/v1/integrations/keys", { name: name.value, scopes });
          load(reveal(t("int.key_new"), k.key, t("int.once")));
        } catch (e) { toast(describeError(e), "error"); }
      });
      const keys = el("section", { class: "card" }, el("h2", {}, icon("plug"), " ", t("int.keys")), el("p", { class: "muted" }, t("int.keys_intro")),
        r.api_keys.length ? el("div", { class: "table-wrap" }, el("table", {}, th(t("int.key_name"), t("int.prefix"), t("int.scopes"), t("int.last_used"), t("c.actions")),
          el("tbody", {}, r.api_keys.map((k) => el("tr", {}, el("td", {}, k.name), el("td", { class: "mono small" }, k.prefix + "…"),
            el("td", {}, k.scopes.map((s) => t("int.scope_" + s)).join(", ")), el("td", {}, fmtDateTime(k.last_used_at)),
            el("td", {}, k.revoked_at ? el("span", { class: "badge" }, t("int.revoked")) : el("button", { class: "btn small danger", type: "button", onclick: async () => {
              if (!(await confirmDialog(t("int.revoke_confirm", { name: k.name }), { danger: true }))) return;
              try { await del(`/api/v1/integrations/keys/${k.id}`); load(); } catch (e) { toast(describeError(e), "error"); }
            } }, t("int.revoke")))))))) : el("p", { class: "muted small" }, t("int.no_keys")),
        kf,
        el("details", {}, el("summary", {}, t("int.examples")),
          el("pre", { class: "code" }, `curl -H "Authorization: Bearer sbk_…" ${base}/api/v1/ext/stations\n\n` +
            `curl -X POST -H "Authorization: Bearer sbk_…" -H "Content-Type: application/json" \\\n  -d '{"minutes": 30, "label": "Besuch"}' ${base}/api/v1/ext/stations/<id>/reservations`),
          el("p", { class: "small muted" }, t("int.endpoints"))));

      // Webhooks
      const url = el("input", { type: "url", required: true, maxlength: "500", placeholder: "https://schul-app.example.org/hooks/fahrrad" });
      const evBoxes = r.events.map((e) => ({ e, box: el("input", { type: "checkbox", checked: ["alert.created", "problem.reported", "gateway.offline"].includes(e) }) }));
      const wf = el("form", {}, field(t("int.url"), url, r.allow_private ? t("int.url_hint_private") : t("int.url_hint")),
        el("fieldset", { class: "field" }, el("legend", {}, t("int.events")),
          el("div", { class: "grid cols-2 checks" }, evBoxes.map(({ e, box }) => el("label", { class: "check" }, box, el("span", {}, t("int.ev_" + e.replace(".", "_")), el("small", { class: "hint mono" }, e)))))),
        el("button", { class: "btn primary", type: "submit" }, t("int.hook_create")));
      wf.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const events = evBoxes.filter((x) => x.box.checked).map((x) => x.e);
        try {
          const w = await post("/api/v1/integrations/webhooks", { url: url.value, events });
          load(reveal(t("int.secret_new"), w.secret, t("int.secret_hint")));
        } catch (e) { toast(describeError(e), "error"); }
      });
      const hookRow = (w) => {
        const last = w.last_delivery;
        const details = el("div");
        return el("li", { class: "hook" },
          el("div", { class: "btn-row" }, el("strong", { class: "mono small" }, w.url),
            el("span", { class: w.active ? "badge ok" : "badge" }, w.active ? t("int.active") : t("int.paused")),
            last ? el("span", { class: last.ok ? "badge ok" : "badge err" }, last.ok ? t("int.last_ok", { t: fmtDateTime(last.at) })
              : t("int.last_fail", { e: last.status_code || last.error || "?" })) : el("span", { class: "badge planned" }, t("int.never"))),
          el("p", { class: "small muted" }, w.events.map((e) => t("int.ev_" + e.replace(".", "_"))).join(" · ")),
          el("div", { class: "btn-row" },
            el("button", { class: "btn small", type: "button", onclick: async () => {
              try {
                const d = (await post(`/api/v1/integrations/webhooks/${w.id}/test`)).deliveries[0];
                toast(d && d.ok ? t("int.test_ok", { s: d.status_code }) : t("int.test_fail", { e: d ? d.status_code || d.error : "?" }), d && d.ok ? "ok" : "error");
                load();
              } catch (e) { toast(describeError(e), "error"); }
            } }, t("int.test")),
            el("button", { class: "btn small", type: "button", onclick: async () => {
              try { await patch(`/api/v1/integrations/webhooks/${w.id}`, { active: !w.active }); load(); } catch (e) { toast(describeError(e), "error"); }
            } }, w.active ? t("int.pause") : t("int.resume")),
            el("button", { class: "btn small", type: "button", onclick: async () => {
              const { deliveries } = await get(`/api/v1/integrations/webhooks/${w.id}/deliveries`);
              clear(details, deliveries.length ? el("ul", { class: "plain-list small" }, deliveries.map((d) => el("li", {},
                fmtDateTime(d.at), " · ", d.event, " · ", d.ok ? "✓ " + d.status_code : "✗ " + (d.status_code || d.error), ` (#${d.attempt})`)))
                : el("p", { class: "small muted" }, t("int.never")));
            } }, t("int.log")),
            el("button", { class: "btn small", type: "button", onclick: async () => {
              try { const s = await post(`/api/v1/integrations/webhooks/${w.id}/secret`); load(reveal(t("int.secret_new"), s.secret, t("int.secret_hint"))); }
              catch (e) { toast(describeError(e), "error"); }
            } }, t("int.rotate")),
            el("button", { class: "btn small danger", type: "button", onclick: async () => {
              if (!(await confirmDialog(t("int.hook_delete_confirm"), { danger: true }))) return;
              try { await del(`/api/v1/integrations/webhooks/${w.id}`); load(); } catch (e) { toast(describeError(e), "error"); }
            } }, t("c.delete"))),
          details);
      };
      const hooks = el("section", { class: "card" }, el("h2", {}, icon("bell"), " ", t("int.hooks")), el("p", { class: "muted" }, t("int.hooks_intro")),
        r.webhooks.length ? el("ul", { class: "plain-list hooks" }, r.webhooks.map(hookRow)) : el("p", { class: "muted small" }, t("int.no_hooks")),
        wf,
        el("details", {}, el("summary", {}, t("int.verify")),
          el("p", { class: "small" }, t("int.verify_text")),
          el("pre", { class: "code" }, "expected = \"sha256=\" + HMAC_SHA256(secret, X-SBB-Timestamp + \".\" + body)\n" +
            "vergleichen mit X-SBB-Signature (zeitkonstant), Zeitstempel max. 5 min alt")));
      clear(node, flash || null, el("p", { class: "muted" }, t("int.intro")), keys, hooks);
    } catch (e) { clear(node, errorCard(e)); }
  };
  load();
  return node;
}

// ---------------------------------------------------------------------- Benachrichtigungen (Mein Konto)
export function notificationsCard() {
  const box = el("section", { class: "card", id: "notifications" }, t("c.loading"));
  get("/api/v1/me/notifications").then(({ prefs, email }) => {
    const mk = (k) => el("input", { type: "checkbox", checked: prefs[k] });
    const alert = mk("alert"), problem = mk("problem"), tech = mk("tech");
    const report = el("select", {}, ["off", "daily", "weekly"].map((v) => el("option", { value: v, selected: prefs.report === v }, t("nt.r_" + v))));
    const f = el("form", {},
      el("label", { class: "check" }, alert, el("span", {}, t("nt.alert"), el("small", { class: "hint" }, t("nt.alert_hint")))),
      el("label", { class: "check" }, problem, el("span", {}, t("nt.problem"), el("small", { class: "hint" }, t("nt.problem_hint")))),
      el("label", { class: "check" }, tech, el("span", {}, t("nt.tech"), el("small", { class: "hint" }, t("nt.tech_hint")))),
      field(t("nt.report"), report, planHas("reports") ? t("nt.report_hint") : t("pk.feature_off", { f: t("feat.reports") })),
      el("div", { class: "btn-row" }, el("button", { class: "btn primary", type: "submit" }, t("c.save")),
        el("button", { class: "btn", type: "button", onclick: async () => {
          try { const r = await post("/api/v1/me/notifications/test"); toast(t("nt.test_sent", { to: r.to })); } catch (e) { toast(describeError(e), "error"); }
        } }, t("nt.test"))));
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await put("/api/v1/me/notifications", { alert: alert.checked, problem: problem.checked, tech: tech.checked, report: report.value });
        toast(t("c.saved"));
      } catch (e) { toast(describeError(e), "error"); }
    });
    clear(box, el("h2", {}, icon("bell"), " ", t("nt.title")), el("p", { class: "muted" }, t("nt.intro", { email })), f);
  }).catch((e) => clear(box, errorCard(e)));
  return box;
}
