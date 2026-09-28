// Landingpage: Sprache, Beispiel-Vorschau, Preise aus der API.
import { applyStatic, getLang, t } from "./i18n.js";
import { el, clear, langSwitcher, slotSymbol } from "./ui.js";
import { planCard } from "./views-admin.js";

const EXAMPLE = [
  { label: "A", state: "occupied" }, { label: "B", state: "free", rec: true }, { label: "C", state: "unknown", reason: "st.r_sensor_error" },
];
let plans = [];

function render() {
  document.documentElement.lang = getLang();
  applyStatic();
  const slotWord = t("ev.slot");
  document.getElementById("preview-free").textContent = t("st.free_of", { f: 1, t: 3 });
  clear(document.getElementById("preview-slots"), EXAMPLE.map((s) => el("li", { class: `slot ${s.state}${s.rec ? " recommended" : ""}` },
    el("span", { class: "name" }, `${slotWord} ${s.label}`),
    el("span", { class: "state" }, el("span", { class: "sym", "aria-hidden": "true" }, slotSymbol(s.state)), t("st." + s.state)),
    s.reason ? el("span", { class: "small" }, t(s.reason)) : null,
    s.rec ? el("span", { class: "badge" }, "→ " + t("st.recommended")) : null)));
  clear(document.getElementById("plans"), plans.map((p) => planCard(p, { featured: p.id === "school",
    action: el("a", { class: `btn ${p.id === "school" ? "primary" : ""}`, href: "/app#/register" }, t("l.choose")) })));
}

clear(document.getElementById("lang"), langSwitcher(render));
fetch("/api/v1/meta", { credentials: "omit" }).then((r) => r.json()).then((m) => { plans = m.plans || []; render(); }).catch(render);
render();
