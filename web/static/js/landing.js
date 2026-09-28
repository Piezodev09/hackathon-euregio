// Landingpage: Sprache, Beispiel-Status (umschaltbar, als Beispiel gekennzeichnet), Preise aus der API.
import { applyStatic, getLang, t } from "./i18n.js";
import { el, clear, langSwitcher, stallStatus } from "./ui.js";
import { planCard } from "./views-admin.js";

const EXAMPLES = {
  free: { state: "free" },
  occupied: { state: "occupied" },
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
    action: el("a", { class: `btn ${p.id === "school" ? "primary" : ""}`, href: "/app#/register" }, t("l.choose")) })));
}

clear(document.getElementById("lang"), langSwitcher(render));
fetch("/api/v1/meta", { credentials: "omit" }).then((r) => r.json()).then((m) => { plans = m.plans || []; render(); }).catch(render);
render();
