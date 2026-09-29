// Hilfe-Menü in der Kopfzeile („?“, auch Taste ?): Tour von vorn, diese Seite erklären, Tour fortsetzen,
// Erste Schritte wieder anzeigen, Tour beim nächsten Anmelden zeigen. Dieselben Aktionen stehen unter „Mein Konto“.
import { patch, post } from "./api.js";
import { t } from "./i18n.js";
import { el, icon, toast } from "./ui.js";
import { state, can, go } from "./state.js";
import { startTour, resumeStep, stepForPath, stepCount, tourRunning } from "./tour.js";

function currentPath() {
  const p = location.hash.replace(/^#/, "").split("?")[0] || "/";
  return p === "" ? "/" : p;
}

/** Liste der Hilfe-Aktionen als [Beschriftung, Funktion]; wird im Menü und auf der Kontoseite genutzt. */
export function helpActions() {
  const acts = [[t("help.restart"), () => startTour(0)]];
  const here = stepForPath(currentPath());
  if (here > 0) acts.push([t("help.this_page"), () => startTour(here)]);
  const resume = resumeStep();
  if (resume > 0) acts.push([t("help.resume", { n: resume + 1, m: stepCount() }), () => startTour(resume)]);
  if (can("admin")) {
    acts.push([t("help.onboarding"), async () => {
      try { await post("/api/v1/onboarding/hide", { hidden: false }); go("/"); toast(t("help.onboarding_done")); }
      catch (_) { toast(t("err.generic"), "error"); }
    }]);
  }
  acts.push([t("help.next_login"), async () => {
    try {
      await patch("/api/v1/auth/me", { tour_done: false });
      if (state.me) state.me.user.tour_done = false;
      toast(t("help.next_login_done"));
    } catch (_) { toast(t("err.generic"), "error"); }
  }]);
  return acts;
}

let openMenu = null;

function closeMenu(focusBtn = true) {
  if (!openMenu) return;
  const { menu, btn } = openMenu;
  menu.remove();
  btn.setAttribute("aria-expanded", "false");
  document.removeEventListener("click", onDocClick, true);
  document.removeEventListener("keydown", onMenuKey);
  openMenu = null;
  if (focusBtn) btn.focus();
}

function onDocClick(e) {
  if (openMenu && !openMenu.menu.contains(e.target) && !openMenu.btn.contains(e.target)) closeMenu(false);
}

function onMenuKey(e) {
  if (!openMenu) return;
  const items = [...openMenu.menu.querySelectorAll("[role=menuitem]")];
  const i = items.indexOf(document.activeElement);
  if (e.key === "Escape") { e.preventDefault(); closeMenu(); }
  else if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
  else if (e.key === "Tab") closeMenu(false);
}

function toggle(btn) {
  if (openMenu) return closeMenu();
  const menu = el("div", { class: "help-menu", role: "menu", "aria-label": t("help.title") },
    helpActions().map(([label, fn]) => el("button", { class: "help-item", type: "button", role: "menuitem",
      onclick: () => { closeMenu(false); fn(); } }, label)));
  btn.after(menu);
  btn.setAttribute("aria-expanded", "true");
  openMenu = { menu, btn };
  document.addEventListener("click", onDocClick, true);
  document.addEventListener("keydown", onMenuKey);
  menu.querySelector("[role=menuitem]").focus();
}

export function helpButton() {
  const btn = el("button", { class: "btn small help-btn", type: "button", "aria-haspopup": "menu", "aria-expanded": "false",
    title: t("help.key"), "data-tour": "help", onclick: () => toggle(btn) }, icon("help"), el("span", {}, t("help.title")));
  return el("div", { class: "help-wrap" }, btn);
}

/** Karte „Hilfe & Tour“ für die Kontoseite. */
export function helpCard() {
  return el("section", { class: "card" }, el("h2", {}, icon("help"), " ", t("help.card")), el("p", { class: "muted" }, t("help.intro")),
    el("div", { class: "btn-row" }, helpActions().map(([label, fn]) => el("button", { class: "btn", type: "button", onclick: fn }, label))));
}

// Taste „?“ öffnet das Menü (nicht in Eingabefeldern, nicht während der Tour)
document.addEventListener("keydown", (e) => {
  if (e.key !== "?" || e.ctrlKey || e.metaKey || e.altKey || tourRunning()) return;
  if (e.target.closest?.("input, textarea, select, [contenteditable]")) return;
  const btn = document.querySelector(".help-btn");
  if (btn) { e.preventDefault(); toggle(btn); }
});
