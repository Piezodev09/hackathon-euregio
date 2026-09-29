// Gemeinsame Bausteine der öffentlichen Seiten (Anlagen-Anzeige /a, Karten-App /k, Status-Seite /status):
// Sprache (DE/NL/EN), Symbole, Token aus dem #-Fragment (geht nie an Server/Proxys, nur als Header an die API).
const NS = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"';
const ICONS = {
  free: `<svg ${NS}><circle cx="24" cy="24" r="20" fill="none" stroke="currentColor" stroke-width="4"/><path d="M14 25l7 7 13-15" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  occupied: `<svg ${NS}><circle cx="12" cy="32" r="8" fill="none" stroke="currentColor" stroke-width="3.5"/><circle cx="36" cy="32" r="8" fill="none" stroke="currentColor" stroke-width="3.5"/><path d="M12 32l8-14h11l5 14M20 18l7 14h-15M31 18l-2-6h5M17 13h6" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  unknown: `<svg ${NS}><circle cx="24" cy="24" r="20" fill="none" stroke="currentColor" stroke-width="4" stroke-dasharray="7 4"/><path d="M18 19a6 6 0 1 1 8.4 5.5c-1.6.8-2.4 2-2.4 3.5v1.5" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><circle cx="24" cy="35.5" r="2.8" fill="currentColor"/></svg>`,
  reserved: `<svg ${NS}><circle cx="24" cy="27" r="16" fill="none" stroke="currentColor" stroke-width="4"/><path d="M24 18v9l6 5" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/><path d="M18 5h12M24 5v6" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>`,
  closed: `<svg ${NS}><circle cx="24" cy="24" r="20" fill="none" stroke="currentColor" stroke-width="4"/><path d="M13 24h22" stroke="currentColor" stroke-width="5" stroke-linecap="round"/></svg>`,
  fault: `<svg ${NS}><path d="M24 5l21 37H3z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/><path d="M24 18v11" stroke="currentColor" stroke-width="4.5" stroke-linecap="round"/><circle cx="24" cy="35" r="2.8" fill="currentColor"/></svg>`,
  bell: `<svg ${NS}><path d="M12 34h24l-3-5V20a9 9 0 0 0-18 0v9z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/><path d="M20 39a4 4 0 0 0 8 0" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>`,
};
const parser = new DOMParser();

export const $ = (id) => document.getElementById(id);

export function icon(name, cls = "") {
  const svg = document.importNode(parser.parseFromString(ICONS[name] || ICONS.unknown, "image/svg+xml").documentElement, true);
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  return svg;
}

export function h(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "class") n.className = v;
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) n.append(c instanceof Node ? c : String(c));
  return n;
}

/** Übersetzungen: dict = {de:{…}, nl:{…}, en:{…}}; Platzhalter {name}. */
export function i18n(dict, onChange) {
  let lang = "de";
  try { lang = localStorage.getItem("lang") || (navigator.language || "de").slice(0, 2); } catch (_) { /* optional */ }
  if (!dict[lang]) lang = "de";
  const t = (key, vars = {}) => String(dict[lang][key] ?? dict.de[key] ?? key).replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
  const apply = () => {
    document.documentElement.lang = lang;
    document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    document.querySelectorAll("[data-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
  };
  document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => {
    lang = b.dataset.lang;
    try { localStorage.setItem("lang", lang); } catch (_) { /* optional */ }
    apply();
    onChange?.();
  }));
  return { t, apply, get lang() { return lang; } };
}

export const eur = (cents, lang) => new Intl.NumberFormat(lang, { style: "currency", currency: "EUR" }).format((cents || 0) / 100);

/** Token aus dem #-Fragment (und optional dauerhaft auf diesem Gerät, für die installierte App). */
export function token(prefix, storeKey) {
  const fromHash = decodeURIComponent(location.hash.replace(/^#/, ""));
  if (fromHash.startsWith(prefix)) {
    if (storeKey) {
      try { localStorage.setItem(storeKey, fromHash); } catch (_) { /* optional */ }
      history.replaceState(null, "", location.pathname);  // Token nicht in der Adresszeile stehen lassen
    }
    return fromHash;
  }
  if (storeKey) {
    try { return localStorage.getItem(storeKey) || ""; } catch (_) { return ""; }
  }
  return "";
}
