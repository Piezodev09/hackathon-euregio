// DOM helpers without innerHTML (XSS-safe: all content as text/nodes), toasts, dialogs, formatting.
import { getLang, setLang, t, STRINGS } from "./i18n.js";

export function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
    else if (k === "checked") e.checked = !!v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
}

export function clear(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

let toastBox;
export function toast(message, kind = "ok") {
  if (!toastBox) {
    toastBox = el("div", { class: "toasts", role: "status", "aria-live": "polite" });
    document.body.append(toastBox);
  }
  const n = el("div", { class: `toast ${kind}` }, message);
  toastBox.append(n);
  setTimeout(() => n.remove(), kind === "error" ? 7000 : 3500);
}

export function confirmDialog(message, { danger = false, confirmLabel } = {}) {
  return new Promise((resolve) => {
    const d = el("dialog", { "aria-modal": "true" },
      el("p", {}, message),
      el("div", { class: "btn-row" },
        el("button", { class: `btn ${danger ? "danger" : "primary"}`, value: "ok", onclick: () => d.close("ok") }, confirmLabel || t("c.confirm")),
        el("button", { class: "btn", value: "cancel", onclick: () => d.close("cancel") }, t("c.cancel"))));
    d.addEventListener("close", () => { resolve(d.returnValue === "ok"); d.remove(); });
    document.body.append(d);
    d.showModal();
  });
}

export function fmtDateTime(iso) {
  if (!iso) return t("c.never");
  return new Date(iso).toLocaleString(getLang(), { dateStyle: "short", timeStyle: "short" });
}
export function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleTimeString(getLang(), { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–";
}
export function fmtMoney(n) {
  return new Intl.NumberFormat(getLang(), { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(n);
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast(t("c.copied")); } catch (_) { /* clipboard may be blocked */ }
}

export function langSwitcher(onChange) {
  const nav = el("div", { class: "lang", role: "group", "aria-label": "Sprache / Taal / Language" });
  const render = () => clear(nav, Object.keys(STRINGS).map((l) =>
    el("button", { type: "button", lang: l, "aria-pressed": String(l === getLang()),
      onclick: () => { setLang(l); render(); onChange && onChange(l); } }, l.toUpperCase())));
  render();
  return nav;
}

// Rough client-side strength meter (the server does the binding check).
export function passwordMeter(input, minLength) {
  const bar = el("span");
  bar.style.width = "0%";
  const label = el("span", { class: "small muted" });
  const wrap = el("div", { class: "hint" }, el("div", { class: "meter", "aria-hidden": "true" }, bar), label);
  const update = () => {
    const v = input.value;
    let score = 0;
    if (v.length >= minLength) score++;
    if (v.length >= minLength + 4) score++;
    if (/[a-z]/.test(v) && /[A-Z]/.test(v)) score++;
    if (/\d/.test(v) && /[^A-Za-z0-9]/.test(v)) score++;
    if (new Set(v).size < 5) score = 0;
    const pct = [5, 30, 55, 80, 100][score];
    bar.style.width = pct + "%";
    label.textContent = v ? `${t("auth.pw_strength")}: ${t(score < 2 ? "auth.pw_weak" : score < 4 ? "auth.pw_ok" : "auth.pw_strong")}` : "";
  };
  input.addEventListener("input", update);
  return wrap;
}

export function field(labelText, input, hint) {
  if (!input.id) input.id = "f_" + Math.random().toString(36).slice(2, 9);
  return el("div", { class: "field" }, el("label", { for: input.id }, labelText), input, hint ? el("div", { class: "hint" }, hint) : null);
}

export function slotSymbol(state) {
  return { free: "✓", occupied: "■", unknown: "?" }[state] || "?";
}
