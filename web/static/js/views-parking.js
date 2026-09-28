// NFC-Karten, Parkvorgänge, Parkgebühren (Organisation -> Radfahrende), Lizenz und Plattform-Rechnungen.
import { get, post, patch, put, del, describeError } from "./api.js";
import { t } from "./i18n.js";
import { el, clear, field, toast, confirmDialog, fmtDateTime, fmtCents, fmtDuration, icon } from "./ui.js";
import { state, can, every } from "./state.js";

const errorCard = (e) => el("div", { class: "alert-box error", role: "alert" }, describeError(e));
const th = (...hs) => el("thead", {}, el("tr", {}, hs.filter(Boolean).map((h) => el("th", { scope: "col" }, h))));
const thisMonth = () => new Date().toISOString().slice(0, 7);
const featureOff = (f) => !state.me?.tenant?.plan?.[f];
const upsell = (f) => el("div", { class: "alert-box info" }, t("pk.feature_off", { f: t("feat." + f) }), " ",
  el("a", { href: "#/billing" }, t("nav.billing")));

function monthPicker(value, onChange) {
  const i = el("input", { type: "month", value, "aria-label": t("pk.month"), onchange: () => onChange(i.value || thisMonth()) });
  return el("label", { class: "inline-field" }, el("span", {}, t("pk.month")), i);
}

// ---------------------------------------------------------------------- Karten
const CARD_STATUS = { pending: "badge warn", active: "badge ok", blocked: "badge err" };

export function viewCards() {
  const list = el("div", { class: "card" }, t("c.loading"));
  const load = async () => {
    try {
      const { cards } = await get("/api/v1/cards");
      const pending = cards.filter((c) => c.status === "pending");
      clear(list,
        pending.length ? el("div", { class: "alert-box warn" }, t("pk.pending_hint", { n: pending.length })) : null,
        cards.length ? el("div", { class: "table-wrap" }, el("table", {},
          th(t("pk.card"), t("c.status"), t("pk.last_seen"), t("pk.parked"), can("admin") ? t("c.actions") : null),
          el("tbody", {}, cards.map((c) => el("tr", {},
            el("td", {}, c.label || el("em", { class: "muted" }, t("pk.unnamed"))),
            el("td", {}, el("span", { class: CARD_STATUS[c.status] }, t("pk.s_" + c.status))),
            el("td", { class: "small" }, fmtDateTime(c.last_seen_at), c.last_station_name ? el("div", { class: "muted" }, c.last_station_name) : null),
            el("td", {}, c.parked ? el("span", { class: "badge ok" }, t("pk.yes_parked")) : "–"),
            can("admin") ? el("td", {}, cardActions(c, load)) : null))))) : el("p", { class: "muted" }, t("pk.no_cards")));
    } catch (e) { clear(list, errorCard(e)); }
  };

  const uid = el("input", { type: "text", required: true, maxlength: "40", placeholder: "04:A1:B2:C3", autocomplete: "off" });
  const label = el("input", { type: "text", required: true, maxlength: "100" });
  const form = el("form", { class: "card" }, el("h2", {}, t("pk.add_card")), el("p", { class: "muted" }, t("pk.add_hint")),
    el("div", { class: "grid cols-2" }, field(t("pk.uid"), uid, t("pk.uid_hint")), field(t("pk.label"), label, t("pk.label_hint"))),
    el("button", { class: "btn primary", type: "submit" }, t("pk.add_card")));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { await post("/api/v1/cards", { uid: uid.value, label: label.value }); uid.value = ""; label.value = ""; toast(t("c.saved")); load(); }
    catch (e) { toast(describeError(e), "error"); }
  });
  every(10000, load);
  return el("div", {}, featureOff("nfc") ? upsell("nfc") : null,
    el("p", { class: "muted" }, t("pk.cards_intro")), list, can("admin") && !featureOff("nfc") ? form : null);
}

function cardActions(c, reload) {
  const act = async (body) => { try { await patch(`/api/v1/cards/${c.id}`, body); reload(); } catch (e) { toast(describeError(e), "error"); } };
  const learn = async () => {
    const name = el("input", { type: "text", required: true, maxlength: "100", value: c.label || "" });
    const d = el("dialog", { "aria-modal": "true" },
      el("form", { method: "dialog" }, el("h2", {}, t("pk.learn")), field(t("pk.label"), name, t("pk.label_hint")),
        el("div", { class: "btn-row" }, el("button", { class: "btn primary", value: "ok" }, t("pk.activate")),
          el("button", { class: "btn", value: "cancel", formnovalidate: true }, t("c.cancel")))));
    d.addEventListener("close", () => { if (d.returnValue === "ok" && name.value.trim()) act({ label: name.value.trim(), status: "active" }); d.remove(); });
    document.body.append(d);
    d.showModal();
  };
  return el("div", { class: "btn-row" },
    c.status === "pending" ? el("button", { class: "btn small primary", type: "button", onclick: learn }, t("pk.learn")) : null,
    c.status === "active" ? el("button", { class: "btn small", type: "button", onclick: learn }, t("pk.rename")) : null,
    c.status === "active" ? el("button", { class: "btn small danger", type: "button", onclick: () => act({ status: "blocked" }) }, t("pk.block")) : null,
    c.status === "blocked" ? el("button", { class: "btn small", type: "button", onclick: () => act({ status: "active" }) }, t("pk.unblock")) : null,
    el("button", { class: "btn small danger", type: "button", onclick: async () => {
      if (!(await confirmDialog(t("pk.delete_confirm", { name: c.label || t("pk.unnamed") }), { danger: true }))) return;
      try { await del(`/api/v1/cards/${c.id}`); reload(); } catch (e) { toast(describeError(e), "error"); }
    } }, t("c.delete")));
}

// ---------------------------------------------------------------------- Parkvorgänge
export function sessionSummary(s) {
  if (!s) return null;
  return el("div", { class: "session-box" }, icon("card"),
    el("div", {},
      el("p", { class: "session-box__title" }, s.card_label ? t("pk.parked_by", { card: s.card_label }) : t("pk.checked_in")),
      el("p", { class: "small" }, t("pk.since", { t: fmtDateTime(s.started_at), d: fmtDuration(s.duration_s) }),
        " · ", el("strong", {}, fmtCents(s.amount_cents)), " ", t("pk.so_far"),
        s.simulated ? [" ", el("span", { class: "badge sim" }, t("c.simulated"))] : null)));
}

export function viewSessions() {
  let month = thisMonth();
  const statusSel = el("select", { "aria-label": t("c.status") },
    ["", "open", "closed", "cancelled"].map((s) => el("option", { value: s }, s ? t("pk.st_" + s) : t("pk.all"))));
  const box = el("div", { class: "card" }, t("c.loading"));
  const load = async () => {
    try {
      const q = new URLSearchParams({ month, limit: "500" });
      if (statusSel.value) q.set("status", statusSel.value);
      const { sessions } = await get(`/api/v1/parking/sessions?${q}`);
      const total = sessions.filter((s) => s.status === "closed").reduce((a, s) => a + (s.amount_cents || 0), 0);
      clear(box, el("p", {}, t("pk.sessions_summary", { n: sessions.length, sum: fmtCents(total) })),
        sessions.length ? el("div", { class: "table-wrap" }, el("table", {},
          th(t("pk.start"), t("pk.end"), t("ev.station"), t("pk.card"), t("pk.duration"), t("pk.amount"), t("c.status"), can("operator") ? t("c.actions") : null),
          el("tbody", {}, sessions.map((s) => el("tr", {},
            el("td", {}, fmtDateTime(s.started_at)), el("td", {}, s.ended_at ? fmtDateTime(s.ended_at) : "–"),
            el("td", {}, s.station_name || "–"), el("td", {}, s.card_label || "–"), el("td", {}, fmtDuration(s.duration_s)),
            el("td", { class: "mono" }, fmtCents(s.amount_cents), s.running ? el("div", { class: "small muted" }, t("pk.so_far")) : null),
            el("td", {}, el("span", { class: s.status === "open" ? "badge ok" : s.status === "cancelled" ? "badge planned" : "badge" }, t("pk.st_" + s.status)),
              s.simulated ? [" ", el("span", { class: "badge sim" }, t("c.simulated"))] : null),
            can("operator") ? el("td", {}, s.status === "open" ? el("div", { class: "btn-row" },
              el("button", { class: "btn small", type: "button", onclick: async () => {
                try { await post(`/api/v1/parking/sessions/${s.id}/close`); load(); } catch (e) { toast(describeError(e), "error"); }
              } }, t("pk.close")),
              can("admin") ? el("button", { class: "btn small danger", type: "button", onclick: async () => {
                if (!(await confirmDialog(t("pk.cancel_confirm"), { danger: true }))) return;
                try { await post(`/api/v1/parking/sessions/${s.id}/cancel`); load(); } catch (e) { toast(describeError(e), "error"); }
              } }, t("pk.cancel")) : null) : "–") : null))))) : el("p", { class: "muted" }, t("pk.no_sessions")));
    } catch (e) { clear(box, errorCard(e)); }
  };
  statusSel.addEventListener("change", load);
  every(10000, load);
  return el("div", {}, el("div", { class: "btn-row toolbar" }, monthPicker(month, (m) => { month = m; load(); }),
    el("label", { class: "inline-field" }, el("span", {}, t("c.status")), statusSel)), box);
}

// ---------------------------------------------------------------------- Parkgebühren
export function tariffText(tf) {
  if (!tf || tf.mode === "free") return t("pk.m_free");
  const parts = [t("pk.m_" + tf.mode, { p: fmtCents(tf.price_cents) })];
  if (tf.free_minutes) parts.push(t("pk.free_min", { n: tf.free_minutes }));
  if (tf.daily_cap_cents) parts.push(t("pk.cap", { p: fmtCents(tf.daily_cap_cents) }));
  return parts.join(" · ");
}

export function tariffForm(tf, onSave, { allowInherit = false } = {}) {
  const cur = tf || { mode: "per_day", price_cents: 50, free_minutes: 15, daily_cap_cents: null };
  const inherit = el("input", { type: "checkbox", checked: allowInherit && !tf });
  const mode = el("select", {}, ["free", "flat", "per_hour", "per_day"].map((m) => el("option", { value: m, selected: m === cur.mode }, t("pk.mode_" + m))));
  const price = el("input", { type: "number", min: "0", max: "1000", step: "0.05", value: (cur.price_cents / 100).toFixed(2) });
  const free = el("input", { type: "number", min: "0", max: "1440", step: "1", value: String(cur.free_minutes || 0) });
  const cap = el("input", { type: "number", min: "0", max: "1000", step: "0.05", value: cur.daily_cap_cents ? (cur.daily_cap_cents / 100).toFixed(2) : "" });
  const fields = el("div", { class: "grid cols-4" }, field(t("pk.mode"), mode), field(t("pk.price"), price, "EUR"),
    field(t("pk.free_minutes"), free), field(t("pk.daily_cap"), cap, t("pk.cap_hint")));
  const sync = () => { fields.hidden = allowInherit && inherit.checked; };
  inherit.addEventListener("change", sync);
  sync();
  const form = el("form", {}, allowInherit ? el("label", { class: "check" }, inherit, el("span", {}, t("pk.inherit"))) : null, fields,
    el("button", { class: "btn primary", type: "submit" }, t("c.save")));
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const c = (v) => Math.round(parseFloat(String(v).replace(",", ".")) * 100);
    onSave(allowInherit && inherit.checked ? null : { mode: mode.value, price_cents: c(price.value) || 0,
      free_minutes: parseInt(free.value, 10) || 0, daily_cap_cents: cap.value ? c(cap.value) : null });
  });
  return form;
}

export function viewParkingBilling() {
  let month = thisMonth();
  const tariffBox = el("section", { class: "card" }, t("c.loading"));
  const statements = el("section", { class: "card" }, t("c.loading"));
  const loadTariff = async () => {
    try {
      const { tariff, enabled } = await get("/api/v1/billing/tariff");
      clear(tariffBox, el("h2", {}, t("pk.tariff")), el("p", {}, el("strong", {}, tariffText(enabled ? tariff : null))),
        enabled ? el("p", { class: "muted small" }, t("pk.tariff_hint")) : upsell("parking_billing"),
        enabled && can("admin") ? tariffForm(tariff, async (tf) => {
          try { await put("/api/v1/billing/tariff", tf); toast(t("c.saved")); loadTariff(); } catch (e) { toast(describeError(e), "error"); }
        }) : null);
    } catch (e) { clear(tariffBox, errorCard(e)); }
  };
  const loadStatements = async () => {
    try {
      const r = await get(`/api/v1/billing/statements?month=${month}`);
      clear(statements, el("h2", {}, t("pk.statements")),
        el("div", { class: "btn-row toolbar" }, monthPicker(month, (m) => { month = m; loadStatements(); }),
          el("a", { class: "btn small", href: `/api/v1/billing/statements?month=${month}&format=csv`, download: "" }, t("pk.csv"))),
        el("p", {}, t("pk.statements_total", { sum: fmtCents(r.total_cents), n: r.statements.length })),
        r.statements.length ? el("div", { class: "table-wrap" }, el("table", {},
          th(t("pk.card"), t("pk.sessions"), t("pk.amount"), t("pk.paid"), can("operator") ? t("c.actions") : null),
          el("tbody", {}, r.statements.map((s) => el("tr", {},
            el("td", {}, s.card_label || t("pk.unnamed"), s.simulated ? [" ", el("span", { class: "badge sim" }, t("c.simulated"))] : null),
            el("td", {}, String(s.sessions)), el("td", { class: "mono" }, fmtCents(s.total_cents)),
            el("td", {}, s.paid_at ? el("span", { class: "badge ok" }, t("pk.paid_on", { t: fmtDateTime(s.paid_at) })) : el("span", { class: "badge warn" }, t("pk.open"))),
            can("operator") ? el("td", {}, el("button", { class: "btn small", type: "button", onclick: async () => {
              try { await post(`/api/v1/billing/statements/${s.card_id}/${month}/paid`, { paid: !s.paid_at }); loadStatements(); }
              catch (e) { toast(describeError(e), "error"); }
            } }, s.paid_at ? t("pk.mark_open") : t("pk.mark_paid"))) : null))))) : el("p", { class: "muted" }, t("pk.no_statements")),
        el("p", { class: "small muted" }, t("pk.no_payment")));
    } catch (e) { clear(statements, errorCard(e)); }
  };
  loadTariff();
  loadStatements();
  return el("div", {}, tariffBox, statements);
}

// ---------------------------------------------------------------------- Lizenz (Kundensicht)
export function licenseCard() {
  const box = el("section", { class: "card" }, t("c.loading"));
  get("/api/v1/org/license").then(({ license: l, current: c, invoices }) => {
    clear(box, el("h2", {}, t("lic.title")),
      el("dl", { class: "sbb-meta" },
        el("div", {}, el("dt", {}, t("lic.status")), el("dd", {}, l.expired ? t("lic.expired") : l.trial ? t("lic.trial") : t("lic.active"),
          el("small", {}, l.valid_until ? t("lic.until", { t: fmtDateTime(l.valid_until), d: l.days_left }) : t("lic.unlimited")))),
        el("div", {}, el("dt", {}, t("lic.price")), el("dd", {}, t("lic.per_day", { p: fmtCents(l.price_per_stall_day_cents) }),
          el("small", {}, t("lic.base", { p: fmtCents(l.base_month_cents) }), l.custom ? " · " + t("lic.custom") : ""))),
        el("div", {}, el("dt", {}, t("lic.month", { m: c.month })), el("dd", { class: "mono" }, fmtCents(c.total_cents),
          el("small", {}, t("lic.stall_days", { n: c.stall_days, d: c.days }))))),
      l.expired ? el("div", { class: "alert-box warn" }, t("lic.expired_hint")) : null,
      invoices.length ? [el("h3", {}, t("lic.invoices")), el("div", { class: "table-wrap" }, el("table", {},
        th(t("lic.number"), t("pk.month"), t("pk.amount"), t("c.status")),
        el("tbody", {}, invoices.map((i) => el("tr", {}, el("td", { class: "mono" }, i.number), el("td", {}, i.month),
          el("td", { class: "mono" }, fmtCents(i.total_cents)), el("td", {}, el("span", { class: i.status === "paid" ? "badge ok" : "badge warn" }, t("lic.inv_" + i.status))))))))]
        : el("p", { class: "small muted" }, t("lic.no_invoices")));
  }).catch((e) => clear(box, errorCard(e)));
  return box;
}

// ---------------------------------------------------------------------- Plattform: Rechnungen & Lizenzen
export function viewPlatformBilling() {
  let month = thisMonth();
  const box = el("div", { class: "card" }, t("c.loading"));
  const load = async () => {
    try {
      const r = await get(`/api/v1/platform/invoices?month=${month}`);
      clear(box, el("div", { class: "btn-row toolbar" }, monthPicker(month, (m) => { month = m; load(); }),
        el("a", { class: "btn small", href: `/api/v1/platform/invoices?month=${month}&format=csv`, download: "" }, t("pk.csv"))),
        el("p", {}, t("pf.inv_total", { sum: fmtCents(r.total_cents) })),
        el("div", { class: "table-wrap" }, el("table", {},
          th(t("org.name"), t("pf.plan"), t("lic.stall_days_h"), t("lic.price"), t("pk.amount"), t("lic.number"), t("c.actions")),
          el("tbody", {}, r.rows.map((x) => {
            const inv = x.invoice;
            return el("tr", {},
              el("td", {}, x.tenant_name, x.license.custom ? el("div", { class: "small muted" }, t("lic.custom")) : null),
              el("td", {}, x.plan), el("td", {}, String(x.stall_days)),
              el("td", { class: "small" }, t("lic.per_day", { p: fmtCents(x.license.price_per_stall_day_cents) }), el("br"), t("lic.base", { p: fmtCents(x.license.base_month_cents) })),
              el("td", { class: "mono" }, fmtCents(inv ? inv.total_cents : x.total_cents), inv ? null : el("div", { class: "small muted" }, t("pf.preview"))),
              el("td", {}, inv ? [el("span", { class: "mono" }, inv.number), " ", el("span", { class: inv.status === "paid" ? "badge ok" : inv.status === "void" ? "badge planned" : "badge warn" }, t("lic.inv_" + inv.status))] : "–"),
              el("td", {}, el("div", { class: "btn-row" },
                !inv ? el("button", { class: "btn small primary", type: "button", disabled: !x.total_cents, onclick: async () => {
                  try { await post("/api/v1/platform/invoices", { tenant_id: x.tenant_id, month }); load(); } catch (e) { toast(describeError(e), "error"); }
                } }, t("pf.issue")) : null,
                inv && inv.status === "open" ? el("button", { class: "btn small", type: "button", onclick: async () => {
                  try { await patch(`/api/v1/platform/invoices/${inv.id}`, { status: "paid" }); load(); } catch (e) { toast(describeError(e), "error"); }
                } }, t("pk.mark_paid")) : null,
                el("button", { class: "btn small", type: "button", onclick: () => editLicense(x, load) }, t("pf.license"))));
          })))));
    } catch (e) { clear(box, errorCard(e)); }
  };
  load();
  return el("div", {}, el("p", { class: "muted" }, t("pf.billing_intro")), box);
}

function editLicense(row, reload) {
  const l = row.license;
  const until = el("input", { type: "date", value: l.valid_until ? l.valid_until.slice(0, 10) : "" });
  const day = el("input", { type: "number", min: "0", step: "0.01", value: (l.price_per_stall_day_cents / 100).toFixed(2) });
  const base = el("input", { type: "number", min: "0", step: "0.01", value: (l.base_month_cents / 100).toFixed(2) });
  const notes = el("input", { type: "text", maxlength: "200", value: l.notes || "" });
  const d = el("dialog", { "aria-modal": "true" }, el("form", { method: "dialog" },
    el("h2", {}, t("pf.license"), " – ", row.tenant_name),
    field(t("lic.valid_until"), until), field(t("lic.price_day_eur"), day), field(t("lic.base_eur"), base), field(t("lic.notes"), notes),
    el("div", { class: "btn-row" }, el("button", { class: "btn primary", value: "ok" }, t("c.save")), el("button", { class: "btn", value: "cancel", formnovalidate: true }, t("c.cancel")))));
  d.addEventListener("close", async () => {
    if (d.returnValue === "ok") {
      const c = (v) => (v === "" ? null : Math.round(parseFloat(v) * 100));
      try {
        await put(`/api/v1/platform/tenants/${row.tenant_id}/license`, { valid_until: until.value || null,
          price_per_stall_day_cents: c(day.value), base_month_cents: c(base.value), notes: notes.value });
        toast(t("c.saved"));
        reload();
      } catch (e) { toast(describeError(e), "error"); }
    }
    d.remove();
  });
  document.body.append(d);
  d.showModal();
}
