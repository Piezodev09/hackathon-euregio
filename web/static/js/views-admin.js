// Verwaltung: Team, Konto & Sicherheit, Organisation, Tarif, Audit-Log, Plattform.
import { api, get, post, patch, del, describeError, setCsrf } from "./api.js";
import { getLang, setLang, t, STRINGS } from "./i18n.js";
import { el, clear, field, toast, confirmDialog, fmtDateTime, fmtMoney, copyText, passwordMeter } from "./ui.js";
import { state, can, go } from "./state.js";
import { licenseCard } from "./views-parking.js";
import { notificationsCard } from "./views-more.js";
import { helpCard } from "./help.js";

const errorCard = (e) => el("div", { class: "alert-box error", role: "alert" }, describeError(e));
const th = (...hs) => el("thead", {}, el("tr", {}, hs.map((h) => el("th", { scope: "col" }, h))));
const RANK = { viewer: 1, operator: 2, admin: 3, owner: 4 };

// Kurzform der Browserkennung für die Sitzungsliste (z. B. "Chrome · Linux").
function shortUa(ua) {
  if (!ua) return "–";
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : ua.split(/[\s/]/)[0];
  const os = /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} · ${os}` : browser;
}

function onSubmit(form, fn) {
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    if (btn) btn.disabled = true;
    try { await fn(); } catch (e) { toast(describeError(e), "error"); } finally { if (btn) btn.disabled = false; }
  });
  return form;
}

async function refreshMe() {
  const me = await get("/api/v1/auth/me");
  state.me = me;
  setCsrf(me.csrf_token);
  return me;
}

// ---------------------------------------------------------------------- Team
export function viewTeam() {
  const users = el("div", { class: "card" }, t("c.loading"));
  const invites = el("div");
  const myRank = RANK[state.me.user.role];

  const load = async () => {
    try {
      const { users: list } = await get("/api/v1/org/users");
      clear(users, el("h2", {}, t("tm.title")), el("div", { class: "table-wrap" }, el("table", {},
        th(t("c.name"), t("c.email"), t("c.role"), t("tm.mfa"), t("tm.last_login"), t("c.actions")),
        el("tbody", {}, list.map((u) => {
          const manageable = can("admin") && !u.is_self && RANK[u.role] <= myRank;
          let role = t("role." + u.role);
          if (manageable) {
            role = el("select", { "aria-label": `${t("c.role")} ${u.email}`, onchange: async (ev) => {
              try { await patch(`/api/v1/org/users/${u.id}`, { role: ev.target.value }); toast(t("c.saved")); } catch (e) { toast(describeError(e), "error"); }
              load();
            } }, Object.keys(RANK).filter((r) => RANK[r] <= myRank).map((r) => el("option", { value: r, selected: r === u.role }, t("role." + r))));
          }
          return el("tr", {}, el("td", {}, u.name, u.is_self ? el("span", { class: "badge" }, " " + t("tm.you")) : null),
            el("td", {}, u.email), el("td", {}, role),
            el("td", {}, u.mfa_enabled ? el("span", { class: "badge ok" }, "✓ 2FA") : el("span", { class: "badge warn" }, "–")),
            el("td", {}, fmtDateTime(u.last_login_at)),
            el("td", {}, manageable ? el("button", { class: "btn small danger", type: "button", onclick: async () => {
              if (!(await confirmDialog(t("tm.remove_confirm", { email: u.email }), { danger: true }))) return;
              try { await del(`/api/v1/org/users/${u.id}`); load(); } catch (e) { toast(describeError(e), "error"); }
            } }, t("tm.remove")) : "–"));
        })))));
      if (can("admin")) {
        const { invitations } = await get("/api/v1/org/invitations");
        clear(invites, invitations.length ? el("div", { class: "card" }, el("h2", {}, t("tm.pending")), el("div", { class: "table-wrap" }, el("table", {},
          th(t("c.email"), t("c.role"), t("tm.expires"), t("c.actions")),
          el("tbody", {}, invitations.map((i) => el("tr", {}, el("td", {}, i.email), el("td", {}, t("role." + i.role)), el("td", {}, fmtDateTime(i.expires_at)),
            el("td", {}, el("button", { class: "btn small", type: "button", onclick: async () => {
              try { await del(`/api/v1/org/invitations/${i.id}`); load(); } catch (e) { toast(describeError(e), "error"); }
            } }, t("tm.withdraw"))))))))) : null);
      }
    } catch (e) { clear(users, errorCard(e)); }
  };

  let inviteForm = null;
  if (can("admin")) {
    const email = el("input", { type: "email", required: true, maxlength: "254", autocomplete: "off" });
    const role = el("select", {}, ["viewer", "operator", "admin"].filter((r) => RANK[r] <= myRank)
      .map((r) => el("option", { value: r }, `${t("role." + r)} – ${t("role.d_" + r)}`)));
    inviteForm = onSubmit(el("form", { class: "card" }, el("h2", {}, t("tm.invite")), el("div", { class: "grid cols-2" },
      field(t("c.email"), email), field(t("c.role"), role)), el("button", { class: "btn primary", type: "submit" }, t("tm.invite_btn"))), async () => {
      await post("/api/v1/org/invitations", { email: email.value, role: role.value });
      toast(t("tm.invited"));
      email.value = "";
      load();
    });
  }
  load();
  return el("div", {}, users, invites, inviteForm);
}

// ---------------------------------------------------------------------- Konto & Sicherheit
export function viewSecurity(rerender) {
  const me = state.me;
  const node = el("div");
  const minLen = 12;

  const banner = me.mfa_setup_required ? el("div", { class: "alert-box warn", role: "alert" }, t("sec.banner")) : null;

  // Profil
  const name = el("input", { type: "text", value: me.user.name, maxlength: "100", required: true });
  const lang = el("select", {}, Object.keys(STRINGS).map((l) => el("option", { value: l, selected: l === getLang() }, l.toUpperCase())));
  const profile = onSubmit(el("form", { class: "card" }, el("h2", {}, t("sec.profile")),
    el("p", { class: "muted" }, `${me.user.email} · ${t("role." + me.user.role)}`),
    el("div", { class: "grid cols-2" }, field(t("c.name"), name), field(t("c.language"), lang)),
    el("button", { class: "btn primary", type: "submit" }, t("c.save"))), async () => {
    await patch("/api/v1/auth/me", { name: name.value, locale: lang.value });
    setLang(lang.value);
    await refreshMe();
    toast(t("c.saved"));
    rerender();
  });

  // Passwort
  const cur = el("input", { type: "password", autocomplete: "current-password", required: true, maxlength: "128" });
  const neu = el("input", { type: "password", autocomplete: "new-password", required: true, maxlength: "128" });
  const pwForm = onSubmit(el("form", { class: "card" }, el("h2", {}, t("sec.pw")),
    el("input", { type: "email", autocomplete: "username", value: me.user.email, hidden: true, readonly: true }),
    field(t("auth.current_password"), cur), field(t("auth.new_password"), neu, t("auth.pw_hint", { n: minLen })), passwordMeter(neu, minLen),
    el("button", { class: "btn primary", type: "submit" }, t("c.save"))), async () => {
    await post("/api/v1/auth/password/change", { current_password: cur.value, new_password: neu.value });
    cur.value = neu.value = "";
    toast(t("sec.pw_changed"));
    loadSessions();
  });

  // 2FA
  const mfa = el("section", { class: "card" });
  const showCodes = (codes) => el("div", { class: "alert-box warn" }, el("h3", {}, t("sec.rc_title")), el("p", {}, t("sec.rc_hint")),
    el("ul", { class: "codes" }, codes.map((c) => el("li", {}, c))),
    el("div", { class: "btn-row" }, el("button", { class: "btn small", type: "button", onclick: () => copyText(codes.join("\n")) }, t("c.copy")),
      el("button", { class: "btn small", type: "button", onclick: () => {
        const url = URL.createObjectURL(new Blob([codes.join("\n") + "\n"], { type: "text/plain" }));
        const a = el("a", { href: url, download: "recovery-codes.txt" });
        document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      } }, "recovery-codes.txt")));
  const renderMfa = (extra) => {
    const on = state.me.user.mfa_enabled;
    const parts = [el("h2", {}, t("sec.mfa")), el("p", {}, el("span", { class: on ? "badge ok" : "badge warn" }, on ? "✓ " + t("sec.mfa_on") : t("sec.mfa_off")))];
    if (!on) {
      parts.push(el("button", { class: "btn primary", type: "button", onclick: async () => {
        try {
          const s = await post("/api/v1/auth/mfa/setup");
          const code = el("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "6", required: true });
          const grouped = s.secret.match(/.{1,4}/g).join(" ");
          const f = onSubmit(el("form", {}, el("p", {}, t("sec.mfa_step1")),
            el("p", { class: "secret-box mono" }, grouped),
            el("div", { class: "btn-row" }, el("button", { class: "btn small", type: "button", onclick: () => copyText(s.secret) }, t("c.copy")),
              el("a", { class: "btn small", href: s.otpauth_uri }, t("sec.mfa_open"))),
            el("p", {}, t("sec.mfa_step2")), field(t("auth.mfa_code"), code),
            el("button", { class: "btn primary", type: "submit" }, t("sec.mfa_enable"))), async () => {
            const r = await post("/api/v1/auth/mfa/enable", { code: code.value });
            await refreshMe();
            renderMfa(showCodes(r.recovery_codes));
            loadSessions();
          });
          renderMfa(f);
          code.focus();
        } catch (e) { toast(describeError(e), "error"); }
      } }, t("sec.mfa_start")));
    } else {
      const pw = el("input", { type: "password", autocomplete: "current-password", required: true });
      const regen = onSubmit(el("form", { class: "btn-row" }, field(t("sec.confirm_pw"), pw),
        el("button", { class: "btn", type: "submit" }, t("sec.rc_regen"))), async () => {
        const r = await post("/api/v1/auth/mfa/recovery-codes", { password: pw.value });
        renderMfa(showCodes(r.recovery_codes));
      });
      parts.push(regen);
      const enforced = state.me.tenant?.mfa_required || state.me.user.is_platform_admin;
      if (!enforced) {
        const pw2 = el("input", { type: "password", autocomplete: "current-password", required: true });
        const code2 = el("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "6", required: true });
        parts.push(onSubmit(el("form", {}, el("h3", {}, t("sec.mfa_disable")), el("div", { class: "grid cols-2" },
          field(t("c.password"), pw2), field(t("auth.mfa_code"), code2)), el("button", { class: "btn danger", type: "submit" }, t("sec.mfa_disable"))), async () => {
          await post("/api/v1/auth/mfa/disable", { password: pw2.value, code: code2.value });
          await refreshMe();
          renderMfa();
        }));
      }
    }
    if (extra) parts.push(extra);
    clear(mfa, parts);
  };
  renderMfa();

  // Sitzungen
  const sessions = el("section", { class: "card" });
  const loadSessions = async () => {
    try {
      const { sessions: list } = await get("/api/v1/auth/sessions");
      clear(sessions, el("h2", {}, t("sec.sessions")), el("div", { class: "table-wrap" }, el("table", {},
        th(t("sec.device"), t("sec.ip"), t("sec.last_active"), t("c.actions")),
        el("tbody", {}, list.map((s) => el("tr", {}, el("td", { class: "small ua", title: s.user_agent || "" }, shortUa(s.user_agent)), el("td", {}, s.ip || "–"),
          el("td", {}, fmtDateTime(s.last_seen_at)),
          el("td", {}, s.current ? el("span", { class: "badge ok" }, t("sec.current")) : el("button", { class: "btn small", type: "button", onclick: async () => {
            try { await del(`/api/v1/auth/sessions/${s.id}`); loadSessions(); } catch (e) { toast(describeError(e), "error"); }
          } }, t("sec.revoke")))))))),
        list.length > 1 ? el("button", { class: "btn", type: "button", onclick: async () => {
          try { await post("/api/v1/auth/sessions/revoke-others"); loadSessions(); } catch (e) { toast(describeError(e), "error"); }
        } }, t("sec.revoke_others")) : null);
    } catch (e) { clear(sessions, errorCard(e)); }
  };
  loadSessions();

  // Konto löschen
  const dpw = el("input", { type: "password", autocomplete: "current-password", required: true });
  const delForm = onSubmit(el("form", { class: "card" }, el("h2", {}, t("sec.delete")), el("p", { class: "muted" }, t("sec.delete_hint")),
    field(t("sec.confirm_pw"), dpw), el("button", { class: "btn danger", type: "submit" }, t("sec.delete"))), async () => {
    if (!(await confirmDialog(t("sec.delete") + "?", { danger: true }))) return;
    await post("/api/v1/auth/me/delete", { password: dpw.value });
    location.href = "/";
  });

  clear(node, banner, me.tenant ? notificationsCard() : null, me.tenant ? helpCard() : null,
    el("div", { class: "grid cols-2" }, el("div", {}, mfa, sessions), el("div", {}, profile, pwForm, delForm)));
  return node;
}

// ---------------------------------------------------------------------- Organisation
export function viewOrg() {
  const tn = state.me.tenant;
  const owner = can("owner");
  const name = el("input", { type: "text", value: tn.name, maxlength: "100", required: true });
  const general = onSubmit(el("form", { class: "card" }, el("h2", {}, t("org.title")),
    el("p", { class: "small muted" }, `${t("org.id")}: `, el("span", { class: "mono" }, tn.id)),
    field(t("org.name"), name), el("button", { class: "btn primary", type: "submit" }, t("c.save"))), async () => {
    await patch("/api/v1/org", { name: name.value });
    await refreshMe();
    toast(t("c.saved"));
  });

  const mfaReq = el("input", { type: "checkbox", checked: tn.mfa_required, disabled: !owner });
  mfaReq.addEventListener("change", async () => {
    try { await patch("/api/v1/org", { mfa_required: mfaReq.checked }); await refreshMe(); toast(t("c.saved")); }
    catch (e) { mfaReq.checked = !mfaReq.checked; toast(describeError(e), "error"); }
  });
  const security = el("section", { class: "card" }, el("h2", {}, t("sec.mfa")),
    el("label", { class: "check" }, mfaReq, el("span", {}, t("org.mfa"))), el("p", { class: "hint" }, t("org.mfa_hint")));

  const exportCard = owner ? el("section", { class: "card" }, el("h2", {}, t("org.export")), el("p", { class: "muted" }, t("org.export_hint")),
    el("button", { class: "btn", type: "button", onclick: async () => {
      try {
        const res = await api("GET", "/api/v1/org/export", undefined, { raw: true });
        const url = URL.createObjectURL(await res.blob());
        const a = el("a", { href: url, download: `export-${tn.id}.json` });
        document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (e) { toast(describeError(e), "error"); }
    } }, t("org.export_btn"))) : null;

  let delCard = null;
  if (owner) {
    const pw = el("input", { type: "password", autocomplete: "current-password", required: true });
    const confirmName = el("input", { type: "text", required: true, autocomplete: "off" });
    delCard = onSubmit(el("form", { class: "card" }, el("h2", {}, t("org.delete")), el("p", { class: "muted" }, t("org.delete_hint")),
      field(t("org.confirm_name"), confirmName, tn.name), field(t("sec.confirm_pw"), pw),
      el("button", { class: "btn danger", type: "submit" }, t("org.delete_btn"))), async () => {
      await post("/api/v1/org/delete", { password: pw.value, confirm_name: confirmName.value });
      location.href = "/";
    });
  }
  return el("div", { class: "grid cols-2" }, el("div", {}, general, security), el("div", {}, exportCard, delCard));
}

// ---------------------------------------------------------------------- Tarif
function planFeatures(p, trialUsed = false) {
  return [t("bill.f_stations", { n: p.max_stations }), t("bill.f_users", { n: p.max_users }),
    t("bill.f_retention", { n: p.retention_days }), p.nfc ? t("feat.nfc") : null, p.parking_billing ? t("feat.parking_billing") : null,
    p.parking_billing ? t("feat.prepaid") : null, p.reservations ? t("feat.reservations") : null, t("feat.hours"), t("feat.notifications"),
    p.reports ? t("feat.reports") : null, p.integrations ? t("feat.integrations") : null,
    p.stall_view ? t("feat.stall_view") : null, p.camera ? t("feat.camera") : null, p.ml_enabled ? t("bill.f_ml") : null,
    p.audit_log ? t("bill.f_audit") : null, p.public_display ? t("bill.f_display") : null,
    p.trial_days && !trialUsed ? t("bill.f_trial", { n: p.trial_days }) : null].filter(Boolean);
}
export function planCard(p, { current, action, featured, trialUsed } = {}) {
  const eur = (c) => new Intl.NumberFormat(getLang(), { style: "currency", currency: "EUR" }).format(c / 100);
  return el("article", { class: `card plan${featured ? " featured" : ""}` },
    el("h3", {}, p.name, current ? [" ", el("span", { class: "badge ok" }, t("bill.current_badge"))] : null,
      featured && !current ? [" ", el("span", { class: "badge" }, t("l.popular"))] : null),
    p.price_per_stall_day_cents || p.base_month_cents
      ? [el("p", { class: "price" }, eur(p.price_per_stall_day_cents), el("span", { class: "small muted" }, " " + t("bill.per_stall_day"))),
        el("p", { class: "small muted" }, t("bill.plus_base", { p: eur(p.base_month_cents) }))]
      : el("p", { class: "price" }, t("bill.free")),
    el("ul", {}, planFeatures(p, trialUsed).map((f) => el("li", {}, f))),
    p.trial_days && trialUsed && !current ? el("p", { class: "small muted" }, t("bill.trial_used")) : null, action || null);
}

// Bestätigungstext mit Hochrechnung (gleiche Formel wie der Rechner auf der Startseite)
function planChangeText(pl, stations, trialUsed) {
  const eur = (c) => new Intl.NumberFormat(getLang(), { style: "currency", currency: "EUR" }).format(c / 100);
  const q = t("bill.confirm", { plan: pl.name });
  if (!pl.base_month_cents && !pl.price_per_stall_day_cents) return `${q} ${t("bill.confirm_free")}`;
  const n = Math.max(1, stations);
  const m = eur(pl.base_month_cents + n * 30 * pl.price_per_stall_day_cents);
  return `${q} ${trialUsed ? t("bill.confirm_paid_now", { m, n }) : t("bill.confirm_trial", { d: pl.trial_days, m, n })}`;
}

export function viewBilling(rerender) {
  const tn = state.me.tenant;
  const u = tn.usage;
  const p = tn.plan;
  const meter = (label, used, max) => {
    const bar = el("span");
    bar.style.width = Math.min(100, Math.round((used / Math.max(1, max)) * 100)) + "%";
    return el("div", { class: "field" }, el("div", { class: "btn-row" }, el("strong", {}, label), el("span", { class: "muted" }, `${used} / ${max}`)),
      el("div", { class: "meter", role: "img", "aria-label": `${label}: ${used} / ${max}` }, bar));
  };
  const usage = el("section", { class: "card" }, el("h2", {}, t("bill.usage")),
    meter(t("bill.stations"), u.stations, p.max_stations), meter(t("bill.users"), u.users, p.max_users),
    el("p", { class: "muted" }, `${t("bill.devices")}: ${u.devices}`));
  const plans = el("div", { class: "grid cols-3" });
  get("/api/v1/org/plans").then(({ plans: list, trial_used: trialUsed }) => {
    clear(plans, list.map((pl) => planCard(pl, { current: pl.id === p.id, featured: pl.id === "school", trialUsed,
      action: pl.id !== p.id && can("owner") ? el("button", { class: "btn primary", type: "button", onclick: async () => {
        if (!(await confirmDialog(planChangeText(pl, u.stations, trialUsed)))) return;
        try { await post("/api/v1/org/plan", { plan: pl.id }); await refreshMe(); toast(t("bill.changed")); rerender(); }
        catch (e) { toast(describeError(e), "error"); }
      } }, t("bill.choose")) : null })));
  }).catch((e) => clear(plans, errorCard(e)));
  return el("div", { class: "page-stack" }, el("div", { class: "grid cols-2" }, el("section", { class: "card" }, el("h2", {}, t("bill.current")), planCard(p, { current: true })), usage),
    licenseCard(),
    el("h2", {}, t("l.pricing")), plans, el("p", { class: "small muted" }, t("bill.note")));
}

// ---------------------------------------------------------------------- Audit-Log
export function viewAudit() {
  const box = el("div", { class: "card" }, t("c.loading"));
  get("/api/v1/org/audit?limit=300").then(({ entries }) => {
    clear(box, el("div", { class: "table-wrap" }, el("table", {}, th(t("au.time"), t("au.actor"), t("au.action"), t("au.target"), t("au.ip")),
      el("tbody", {}, entries.map((e) => el("tr", {}, el("td", {}, fmtDateTime(e.at)), el("td", {}, e.actor || "–"),
        el("td", { class: "mono small" }, e.action), el("td", { class: "small" }, e.target || "–"), el("td", {}, e.ip || "–")))))));
  }).catch((e) => clear(box, errorCard(e), e.code === "plan_feature" ? el("a", { class: "btn", href: "#/billing" }, t("nav.billing")) : null));
  return box;
}

// ---------------------------------------------------------------------- Plattform
export function viewPlatform() {
  const kpis = el("div", { class: "grid cols-4" });
  const table = el("div", { class: "card" }, t("c.loading"));
  const load = async () => {
    try {
      const [stats, { tenants }] = await Promise.all([get("/api/v1/platform/stats"), get("/api/v1/platform/tenants")]);
      const k = (v, l) => el("div", { class: "card kpi" }, el("span", { class: "value" }, String(v)), el("span", { class: "label" }, l));
      clear(kpis, k(stats.tenants, t("pf.tenants")), k(stats.active_tenants, t("pf.active")), k(fmtMoney(stats.mrr_eur), t("pf.mrr")),
        k(stats.users, t("pf.users")), k(stats.stations, t("ov.stations")), k(stats.devices_online, t("pf.devices_online")),
        k(stats.measurements_24h, t("pf.meas24")));
      clear(table, el("h2", {}, t("pf.tenants")), el("div", { class: "table-wrap" }, el("table", {},
        th(t("org.name"), t("pf.owner"), t("pf.plan"), t("c.status"), t("ov.stations"), t("pf.users"), t("c.created"), t("c.actions")),
        el("tbody", {}, tenants.map((tn) => el("tr", {},
          el("td", {}, tn.name, el("div", { class: "mono small muted" }, tn.id)), el("td", {}, tn.owner_email || "–"),
          el("td", {}, el("select", { "aria-label": t("pf.plan"), onchange: async (ev) => {
            try { await patch(`/api/v1/platform/tenants/${tn.id}`, { plan: ev.target.value }); toast(t("c.saved")); } catch (e) { toast(describeError(e), "error"); }
            load();
          } }, ["free", "school", "pro"].map((p) => el("option", { value: p, selected: p === tn.plan }, p)))),
          el("td", {}, tn.status === "active" ? el("span", { class: "badge ok" }, t("pf.active_s")) : el("span", { class: "badge err" }, t("pf.suspended"))),
          el("td", {}, String(tn.usage.stations)), el("td", {}, String(tn.usage.users)), el("td", {}, fmtDateTime(tn.created_at)),
          el("td", {}, el("button", { class: `btn small${tn.status === "active" ? " danger" : ""}`, type: "button", onclick: async () => {
            const suspend = tn.status === "active";
            if (suspend && !(await confirmDialog(t("pf.suspend_confirm", { name: tn.name }), { danger: true }))) return;
            try { await patch(`/api/v1/platform/tenants/${tn.id}`, { status: suspend ? "suspended" : "active" }); load(); }
            catch (e) { toast(describeError(e), "error"); }
          } }, tn.status === "active" ? t("pf.suspend") : t("pf.activate")))))))));
    } catch (e) { clear(table, errorCard(e)); }
  };
  load();
  return el("div", {}, kpis, table);
}
