// Landingpage: Sprache, Beispiel-Status (umschaltbar, als Beispiel gekennzeichnet), Preise aus der API.
import { applyStatic, getLang, t } from "./i18n.js";
import { el, clear, langSwitcher, stallStatus } from "./ui.js";
import { planCard } from "./views-admin.js";

const EXAMPLES = {
  free: { state: "free" },
  occupied: { state: "occupied" },
  reserved: { state: "reserved", reservation: { until: new Date(Date.now() + 25 * 60000).toISOString() } },
  unknown: { state: "unknown", unknown_reason: "stale", stale_after_s: 30 },
};
let example = "free";
let plans = [];

function render() {
  document.documentElement.lang = getLang();
  applyStatic();
  const card = stallStatus(EXAMPLES[example]);
  card.append(el("span", { class: "sbb-status__corner sbb-tag sbb-tag--demo" }, t("l.preview_sim")));
  clear(document.getElementById("preview"), card);
  clear(document.getElementById("preview-switch"), Object.keys(EXAMPLES).map((k) =>
    el("button", { class: "btn small", type: "button", "aria-pressed": String(k === example),
      onclick: () => { example = k; render(); document.querySelector(`[data-example="${k}"]`)?.focus(); }, "data-example": k }, t("st." + k))));
  clear(document.getElementById("plans"), plans.map((p) => planCard(p, { featured: p.id === "school",
    action: el("a", { class: `btn ${p.id === "school" ? "primary" : ""}`, href: `/app#/register?plan=${p.id}` }, t("l.choose")) })));
  renderCalc();
}

// Kostenrechner: Grundgebühr + Stellplätze x 30 Tage x Tagespreis (Richtwert, zzgl. MwSt.)
function renderCalc() {
  const sel = document.getElementById("calc-plan");
  const n = document.getElementById("calc-stalls");
  const paid = plans.filter((p) => p.price_per_stall_day_cents || p.base_month_cents);
  if (!paid.length) return;
  if (sel.options.length !== paid.length) {
    clear(sel, paid.map((p) => el("option", { value: p.id, selected: p.id === "school" }, p.name)));
  }
  const p = paid.find((x) => x.id === sel.value) || paid[0];
  const stalls = Math.max(1, Math.min(p.max_stations, parseInt(n.value, 10) || 1));
  const eur = (c) => new Intl.NumberFormat(getLang(), { style: "currency", currency: "EUR" }).format(c / 100);
  const month = p.base_month_cents + stalls * 30 * p.price_per_stall_day_cents;
  document.getElementById("calc-result").textContent = t("l.calc_result", { m: eur(month), y: eur(month * 12), n: stalls });
}
document.getElementById("calc-plan").addEventListener("change", renderCalc);
document.getElementById("calc-stalls").addEventListener("input", renderCalc);

clear(document.getElementById("lang"), langSwitcher(render));
fetch("/api/v1/meta", { credentials: "omit" }).then((r) => r.json()).then((m) => { plans = m.plans || []; render(); }).catch(render);
render();
