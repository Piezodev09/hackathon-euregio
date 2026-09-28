// Landing page: languages, live demo station, pricing with monthly/yearly toggle, demo request form.
import { STRINGS, applyStatic, getLang, planName, t } from "./i18n.js";
import { LANDING } from "./landing-i18n.js";
import { el, clear, langSwitcher, slotSymbol, fmtTime } from "./ui.js";

// English static texts come from the page itself (single source, works without JavaScript).
for (const node of document.querySelectorAll("[data-i18n]")) STRINGS.en[node.dataset.i18n] ??= node.textContent.trim();
for (const node of document.querySelectorAll("[data-i18n-aria]")) STRINGS.en[node.dataset.i18nAria] ??= node.getAttribute("aria-label");
for (const node of document.querySelectorAll("[data-i18n-alt]")) STRINGS.en[node.dataset.i18nAlt] ??= node.getAttribute("alt");
for (const [lang, table] of Object.entries(LANDING)) Object.assign(STRINGS[lang], table);

const money = (v, digits = 0) => new Intl.NumberFormat(getLang(), { style: "currency", currency: "EUR",
  minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v);

let meta = { plans: [], demo: null };
let metaLoaded = false;
let period = "month";

// ---------------------------------------------------------------------- live demo
const live = { status: null, okAt: 0, timer: null };
const EXAMPLE = {
  display_name: "Schoolyard – main entrance", total: 6, recommendation: "C", simulated_data: false, stale_after_s: 30,
  slots: ["occupied", "occupied", "free", "occupied", "unknown", "free"].map((state, i) => ({
    slot_id: "ABCDEF"[i], label: `Space ${"ABCDEF"[i]}`, state, unknown_reason: state === "unknown" ? "sensor_error" : null, alert: null })),
};

function renderLive() {
  if (!metaLoaded) return;
  const box = document.getElementById("live-card");
  const example = !meta.demo;
  const st = example ? EXAMPLE : live.status;
  if (!st) return;
  const lost = !example && Date.now() - live.okAt > (st.stale_after_s || 30) * 1000;
  const slots = st.slots.map((s) => (lost ? { ...s, state: "unknown", unknown_reason: "connection" } : s));
  const free = slots.filter((s) => s.state === "free");
  const rec = !lost && st.recommendation ? slots.find((s) => s.slot_id === st.recommendation && s.state === "free") : null;
  clear(box,
    el("div", { class: "live-head" },
      el("h3", {}, st.display_name),
      example ? el("span", { class: "badge" }, t("l.live_example"))
        : st.simulated_data ? el("span", { class: "badge sim" }, t("l.live_sim")) : null),
    el("p", { class: "live-free" }, t("l.live_free", { f: free.length, t: slots.length })),
    el("p", { class: "live-rec" }, lost ? t("l.live_conn_lost") : rec ? "→ " + t("l.live_rec", { s: rec.label }) : t("l.live_rec_none")),
    el("ul", { class: "slots live-slots" }, slots.map((s) => {
      const isRec = rec && s.slot_id === rec.slot_id;
      return el("li", { class: `slot ${s.state}${isRec ? " recommended" : ""}${s.alert ? " alerting" : ""}` },
        el("span", { class: "name" }, s.label),
        el("span", { class: "state" }, el("span", { class: "sym", "aria-hidden": "true" }, slotSymbol(s.state)), t("st." + s.state)),
        s.state === "unknown" && s.unknown_reason ? el("span", { class: "small" }, t("st.r_" + s.unknown_reason)) : null,
        isRec ? el("span", { class: "badge" }, "→ " + t("st.recommended")) : null,
        s.alert ? el("span", { class: "badge warn" }, "⚠ " + t("st.alert")) : null);
    })),
    example ? null : el("p", { class: "small muted" }, t("l.live_updated", { t: fmtTime(st.server_time) })));
}

async function pollLive() {
  try {
    const r = await fetch("/api/v1/public/display/status", { cache: "no-store", credentials: "omit",
      headers: { "X-Display-Token": meta.demo.token } });
    if (r.ok) { live.status = await r.json(); live.okAt = Date.now(); }
  } catch (_) { /* shown as "connection lost" after stale_after_s */ }
  renderLive();
  live.timer = setTimeout(pollLive, document.hidden ? 15000 : 3000);
}

function setupLive() {
  if (meta.demo) {
    const qr = document.getElementById("live-qr");
    document.getElementById("live-qr-img").src = meta.demo.qr;
    document.getElementById("live-qr-link").href = meta.demo.display_url;
    qr.hidden = false;
    pollLive();
    setInterval(renderLive, 1000);
  } else {
    renderLive();
  }
}

// ---------------------------------------------------------------------- pricing
function planFeatures(p) {
  return [t("bill.f_stations", { n: p.max_stations }), t("bill.f_slots", { n: p.max_slots_per_station }),
    t("bill.f_users", { n: p.max_users }), t("bill.f_retention", { n: p.retention_days }),
    p.ml_enabled ? t("bill.f_ml") : null, p.audit_log ? t("bill.f_audit") : null, p.public_display ? t("bill.f_display") : null]
    .filter(Boolean);
}

function planCard(p) {
  const featured = p.id === "school";
  const monthly = period === "year" ? (p.price_eur_month * 10) / 12 : p.price_eur_month;
  const price = p.price_eur_month
    ? [el("span", { class: "amount" }, money(monthly, period === "year" ? 2 : 0)), el("span", { class: "per" }, " " + t("l.per_month"))]
    : [el("span", { class: "amount" }, money(0))];
  return el("article", { class: `card plan${featured ? " featured" : ""}` },
    featured ? el("span", { class: "ribbon" }, t("l.popular")) : null,
    el("h3", {}, planName(p)),
    el("p", { class: "price" }, price),
    p.price_eur_month && period === "year" ? el("p", { class: "small muted" }, t("l.billed_yearly", { p: money(p.price_eur_month * 10) })) : null,
    p.price_eur_month ? el("p", { class: "small per-station" }, t("l.per_station", { p: money(monthly / p.max_stations, 2) })) : null,
    el("ul", {}, planFeatures(p).map((f) => el("li", {}, f))),
    el("a", { class: `btn${featured ? " primary" : ""}`, href: "/app#/register" }, t("l.choose", { plan: planName(p) })));
}

function enterpriseCard() {
  return el("article", { class: "card plan enterprise" },
    el("h3", {}, t("l.enterprise_t")),
    el("p", { class: "price" }, el("span", { class: "amount" }, t("l.enterprise_price"))),
    el("ul", {}, [t("l.enterprise_1"), t("l.enterprise_2"), t("l.enterprise_3")].map((f) => el("li", {}, f))),
    el("a", { class: "btn", href: "#contact" }, t("l.contact_us")));
}

function renderPlans() {
  document.querySelectorAll("[data-period]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.period === period)));
  clear(document.getElementById("plans"), meta.plans.map(planCard), enterpriseCard());
}

document.querySelectorAll("[data-period]").forEach((b) => b.addEventListener("click", () => { period = b.dataset.period; renderPlans(); }));

// ---------------------------------------------------------------------- demo request
const form = document.getElementById("lead-form");
form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const status = document.getElementById("lead-status");
  const show = (key, kind) => clear(status, el("div", { class: `alert-box ${kind}` }, t(key)));
  form.querySelectorAll("[aria-invalid]").forEach((n) => n.removeAttribute("aria-invalid"));
  const fd = new FormData(form);
  const bad = ["name", "organisation", "email"].filter((k) => !form.elements[k].checkValidity() || !String(fd.get(k)).trim());
  bad.forEach((k) => form.elements[k].setAttribute("aria-invalid", "true"));
  if (bad.length) { form.elements[bad[0]].focus(); return show("l.form_invalid", "error"); }
  if (!form.elements.consent.checked) { form.elements.consent.focus(); return show("l.form_consent_missing", "error"); }
  const btn = form.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const r = await fetch("/api/v1/leads", { method: "POST", credentials: "omit", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: fd.get("name"), organisation: fd.get("organisation"), email: fd.get("email"),
        message: fd.get("message") || "", consent: true, website: fd.get("website") || "", locale: getLang() }) });
    if (r.status === 202) { form.reset(); show("l.form_ok", "ok"); }
    else if (r.status === 429) show("l.form_rate", "error");
    else if (r.status === 422) show("l.form_invalid", "error");
    else show("l.form_error", "error");
  } catch (_) { show("l.form_error", "error"); }
  finally { btn.disabled = false; }
});

// ---------------------------------------------------------------------- language
function render() {
  document.documentElement.lang = getLang();
  applyStatic();
  document.querySelectorAll("[data-i18n-aria]").forEach((n) => n.setAttribute("aria-label", t(n.dataset.i18nAria)));
  document.querySelectorAll("[data-i18n-alt]").forEach((n) => n.setAttribute("alt", t(n.dataset.i18nAlt)));
  renderPlans();
  renderLive();
}

document.getElementById("year").textContent = String(new Date().getFullYear());
clear(document.getElementById("lang"), langSwitcher(render));
render();
fetch("/api/v1/meta", { credentials: "omit" }).then((r) => r.json()).then((m) => {
  meta = { ...meta, ...m };
  metaLoaded = true;
  render();
  setupLive();
}).catch(() => { metaLoaded = true; setupLive(); });
