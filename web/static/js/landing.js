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

// Kostenrechner: Grundgebühr + Stellplätze x 30 Tage x Tagespreis (Richtwert, zzgl. MwSt.).
// Passt die Anzahl nicht in den gewählten Tarif, wird nicht still gekappt, sondern der passende Tarif empfohlen.
const monthCents = (p, n) => p.base_month_cents + n * 30 * p.price_per_stall_day_cents;

function renderCalc() {
  const sel = document.getElementById("calc-plan");
  const n = document.getElementById("calc-stalls");
  const note = document.getElementById("calc-note");
  const paid = plans.filter((p) => p.price_per_stall_day_cents || p.base_month_cents);
  if (!paid.length) return;
  if (sel.options.length !== paid.length) {
    clear(sel, paid.map((p) => el("option", { value: p.id, selected: p.id === "school" }, p.name)));
  }
  const maxAll = Math.max(...paid.map((x) => x.max_stations));
  n.max = String(maxAll);
  const p = paid.find((x) => x.id === sel.value) || paid[0];
  const stalls = Math.max(1, Math.min(maxAll, parseInt(n.value, 10) || 1));
  const eur = (c) => new Intl.NumberFormat(getLang(), { style: "currency", currency: "EUR" }).format(c / 100);
  const fitting = paid.filter((x) => x.max_stations >= stalls);
  const best = fitting.sort((a, b) => monthCents(a, stalls) - monthCents(b, stalls))[0];
  const result = document.getElementById("calc-result");
  note.hidden = true;
  if (stalls > p.max_stations) {
    result.textContent = t("l.calc_too_many", { plan: p.name, max: p.max_stations });
    note.hidden = false;
    clear(note, t("l.calc_use", { plan: best.name, m: eur(monthCents(best, stalls)) }), " ",
      el("button", { class: "btn link", type: "button", onclick: () => { sel.value = best.id; renderCalc(); } }, t("l.calc_switch", { plan: best.name })));
    return;
  }
  const month = monthCents(p, stalls);
  result.textContent = t("l.calc_result", { m: eur(month), y: eur(month * 12), n: stalls });
  if (best && best.id !== p.id) {
    note.hidden = false;
    clear(note, t("l.calc_cheaper", { plan: best.name, m: eur(monthCents(best, stalls)) }), " ",
      el("button", { class: "btn link", type: "button", onclick: () => { sel.value = best.id; renderCalc(); } }, t("l.calc_switch", { plan: best.name })));
  }
}
document.getElementById("calc-plan").addEventListener("change", renderCalc);
document.getElementById("calc-stalls").addEventListener("input", renderCalc);

clear(document.getElementById("lang"), langSwitcher(render));
fetch("/api/v1/meta", { credentials: "omit" }).then((r) => r.json()).then((m) => { plans = m.plans || []; render(); }).catch(render);
render();
