// Kundenportal: Router, Layout, Rechteprüfung in der Oberfläche (maßgeblich ist immer der Server).
import { get, post, setCsrf, setAuthLostHandler } from "./api.js";
import { applyStatic, getLang, setLang, t } from "./i18n.js";
import { el, clear, langSwitcher, icon } from "./ui.js";
import { state, can, clearTimers, go } from "./state.js";
import * as A from "./views-auth.js";
import * as S from "./views-station.js";
import * as M from "./views-admin.js";

const root = document.getElementById("root");
const PUBLIC = new Set(["login", "register", "check-email", "verify", "forgot", "reset", "invite"]);

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query] = raw.split("?");
  return { parts: path.split("/").filter(Boolean), params: new URLSearchParams(query || "") };
}

function navItems() {
  const me = state.me;
  const items = [];
  if (me.tenant) {
    items.push(["section", t("nav.s_monitor")], ["/", t("nav.overview")], ["/events", t("nav.events")]);
    items.push(["section", t("nav.s_admin")], ["/stations", t("nav.stations")], ["/devices", t("nav.devices")], ["/team", t("nav.team")]);
    if (can("admin")) items.push(["/org", t("nav.org")], ["/audit", t("nav.audit")]);
    items.push(["/billing", t("nav.billing")]);
  }
  if (me.user.is_platform_admin) items.push(["section", t("nav.s_platform")], ["/platform", t("nav.platform")]);
  items.push(["section", ""], ["/security", t("nav.security")]);
  return items;
}

function layout(title, content, currentPath, actions) {
  const me = state.me;
  const sidebar = el("nav", { class: "sidebar", id: "sidebar", "aria-label": t("c.menu") },
    el("a", { class: "brand", href: "#/" }, icon("logo", "logo"), me.product_name),
    me.tenant ? el("p", { class: "small muted", style: null }, me.tenant.name, " · ", el("span", { class: "badge" }, me.tenant.plan.name)) : null,
    el("ul", { class: "nav" }, navItems().map(([href, label]) => href === "section"
      ? el("li", { class: "section" }, label)
      : el("li", {}, el("a", { href: "#" + href, "aria-current": currentPath === href ? "page" : null }, label)))),
    el("div", { class: "foot" },
      el("p", {}, el("strong", {}, me.user.name), el("br"), el("span", { class: "small muted" }, me.user.email)),
      el("div", { class: "btn-row" }, langSwitcher(() => route()),
        el("button", { class: "btn small", type: "button", onclick: logout }, t("c.logout")))));
  const toggle = el("button", { class: "btn small menu-toggle", type: "button", "aria-controls": "sidebar", "aria-expanded": "false",
    onclick: () => { const open = sidebar.classList.toggle("open"); toggle.setAttribute("aria-expanded", String(open)); } }, t("c.menu"));
  sidebar.addEventListener("click", (e) => { if (e.target.closest("a")) sidebar.classList.remove("open"); });
  const h1 = el("h1", { tabindex: "-1", id: "page-title" }, title);
  const main = el("main", { class: "main", id: "main" },
    el("div", { class: "topbar" }, el("div", { class: "btn-row" }, toggle, h1), actions || null),
    me.tenant && me.tenant.status !== "active" ? el("div", { class: "alert-box error" }, t("err.tenant_suspended")) : null,
    content);
  return { node: el("div", { class: "shell" }, el("a", { class: "skip", href: "#main" }, t("c.skip")), sidebar, main), h1 };
}

async function logout() {
  try { await post("/api/v1/auth/logout"); } catch (_) {}
  state.me = null;
  setCsrf(null);
  go("/login");
}

function setTitleFactory(h1) {
  return (text) => {
    if (h1.textContent !== text) h1.textContent = text;
    document.title = `${text} · ${state.me?.product_name || ""}`;
  };
}

export async function route() {
  clearTimers();
  applyStatic();
  const { parts, params } = parseHash();
  const name = parts[0] || "";

  if (PUBLIC.has(name)) {
    if (state.me && (name === "login" || name === "register")) return go("/");
    const rerender = () => route();
    const views = { login: () => A.viewLogin(rerender), register: () => A.viewRegister(rerender), "check-email": () => A.viewCheckEmail(rerender),
      verify: () => A.viewVerify(params, rerender), forgot: () => A.viewForgot(rerender), reset: () => A.viewReset(params, rerender),
      invite: () => A.viewInvite(params, rerender) };
    clear(root, views[name]());
    document.title = t("auth." + (name === "login" ? "login_title" : name === "register" ? "register_title" : "login_title"));
    root.querySelector("h1")?.focus();
    return;
  }

  if (!state.me) {
    try {
      state.me = await get("/api/v1/auth/me");
      setCsrf(state.me.csrf_token);
      if (state.me.user.locale && !localStorage.getItem("lang")) setLang(state.me.user.locale);
    } catch (_) {
      try { sessionStorage.setItem("returnTo", "/" + parts.join("/")); } catch (_) {}
      return go("/login");
    }
  }
  const me = state.me;
  if (me.mfa_setup_required && name !== "security") return go("/security");
  if (!me.tenant && me.user.is_platform_admin && !["platform", "security"].includes(name)) return go("/platform");

  const rerender = () => route();
  let title = "";
  let view;
  let current = "/" + name;
  let actions = null;
  const deny = () => el("div", { class: "alert-box error" }, t("err.forbidden"));

  if (name === "") {
    title = t("ov.title"); current = "/";
    view = S.viewOverview();
    if (can("admin")) actions = el("a", { class: "btn primary", href: "#/stations/new" }, "+ " + t("ov.new_station"));
  } else if (name === "stations" && parts[1] === "new") {
    title = t("ss.new_title"); current = "/stations";
    view = can("admin") ? S.viewNewStation() : deny();
  } else if (name === "stations" && parts[1] && parts[2] === "settings") {
    title = t("st.settings"); current = "/stations";
    view = null;
  } else if (name === "stations" && parts[1]) {
    title = t("c.loading"); current = "/stations";
    view = null;
    if (can("admin")) actions = el("a", { class: "btn", href: `#/stations/${parts[1]}/settings` }, t("st.settings"));
  } else if (name === "stations") {
    title = t("nav.stations");
    view = S.viewStations();
    if (can("admin")) actions = el("a", { class: "btn primary", href: "#/stations/new" }, "+ " + t("ov.new_station"));
  } else if (name === "events") { title = t("ev.title"); view = S.viewEvents(); }
  else if (name === "devices") { title = t("nav.devices"); view = S.viewDevices(); }
  else if (name === "team") { title = t("tm.title"); view = M.viewTeam(); }
  else if (name === "security") { title = t("sec.title"); view = M.viewSecurity(rerender); }
  else if (name === "org") { title = t("org.title"); view = can("admin") ? M.viewOrg() : deny(); }
  else if (name === "billing") { title = t("bill.title"); view = M.viewBilling(rerender); }
  else if (name === "audit") { title = t("au.title"); view = can("admin") ? M.viewAudit() : deny(); }
  else if (name === "platform") { title = t("pf.title"); view = me.user.is_platform_admin ? M.viewPlatform() : deny(); }
  else { title = t("err.not_found"); view = el("p", {}, el("a", { href: "#/" }, t("nav.overview"))); }

  const placeholder = el("div");
  const { node, h1 } = layout(title, view || placeholder, current, actions);
  const setTitle = setTitleFactory(h1);
  if (!view && parts[2] === "settings") placeholder.append(can("admin") ? S.viewStationSettings(parts[1], setTitle) : deny());
  else if (!view) placeholder.append(S.viewStation(parts[1], setTitle));
  clear(root, node);
  setTitle(title);
  h1.focus({ preventScroll: true });
}

setAuthLostHandler((err) => {
  if (err.code === "mfa_setup_required") { if (state.me) state.me.mfa_setup_required = true; return go("/security"); }
  if (err.code === "tenant_suspended") return;
  state.me = null;
  setCsrf(null);
  const { parts } = parseHash();
  if (!PUBLIC.has(parts[0] || "")) go("/login");
});

window.addEventListener("hashchange", route);
document.documentElement.lang = getLang();
A.loadMeta().then(() => { if (!location.hash) location.hash = "#/"; else route(); });
