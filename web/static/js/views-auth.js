// Öffentliche Seiten: Anmelden (inkl. 2FA), Registrieren, E-Mail bestätigen, Passwort, Einladung.
import { api, post, describeError, setCsrf } from "./api.js";
import { t } from "./i18n.js";
import { el, clear, field, langSwitcher, passwordMeter, icon } from "./ui.js";
import { state, go } from "./state.js";

let meta = { password_min_length: 12, signup_enabled: true, product_name: "Smart Bicycle Box" };
export async function loadMeta() {
  try { meta = await api("GET", "/api/v1/meta"); } catch (_) {}
  return meta;
}

function authCard(title, body, rerender) {
  return el("div", { class: "auth-wrap" },
    el("main", { class: "auth-card", id: "main" },
      el("a", { class: "brand", href: "/" }, icon("logo", "logo"), meta.product_name),
      el("div", { class: "card" },
        el("div", { class: "btn-row", style: null }, el("h1", { tabindex: "-1" }, title)),
        body),
      el("div", { class: "btn-row", "aria-label": t("c.language") }, langSwitcher(rerender))));
}

function errorBox() {
  const box = el("div", { class: "alert-box error", role: "alert", hidden: true });
  box.show = (msg) => { box.textContent = msg; box.hidden = false; };
  box.hide = () => { box.hidden = true; };
  return box;
}

function input(type, name, attrs = {}) {
  return el("input", { type, name, required: true, ...attrs });
}

function submitting(form, fn) {
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    try { await fn(new FormData(form)); } finally { btn.disabled = false; }
  });
  return form;
}

// Token aus dem URL-Fragment lesen und sofort aus Adresszeile/Verlauf entfernen.
export function takeToken(params, route) {
  const token = params.get("token");
  if (token) history.replaceState(null, "", `/app#/${route}`);
  return token || sessionStorage.getItem("tok_" + route) || "";
}
function keepToken(route, token) {
  try { token ? sessionStorage.setItem("tok_" + route, token) : sessionStorage.removeItem("tok_" + route); } catch (_) {}
}

export function afterLogin(me) {
  state.me = me;
  setCsrf(me.csrf_token);
  let target = "/";
  try { target = sessionStorage.getItem("returnTo") || "/"; sessionStorage.removeItem("returnTo"); } catch (_) {}
  go(me.mfa_setup_required ? "/security" : me.user.is_platform_admin && !me.tenant ? "/platform" : target);
}

// ---------------------------------------------------------------------- Anmelden
export function viewLogin(rerender) {
  const err = errorBox();
  const extra = el("div");
  const email = input("email", "email", { autocomplete: "username", autofocus: true });
  const pw = input("password", "password", { autocomplete: "current-password" });
  const form = submitting(el("form", { novalidate: false },
    err, field(t("c.email"), email), field(t("c.password"), pw), extra,
    el("button", { class: "btn primary", type: "submit" }, t("auth.login_btn"))), async (fd) => {
    err.hide();
    clear(extra);
    try {
      const r = await post("/api/v1/auth/login", { email: fd.get("email"), password: fd.get("password") });
      if (r.mfa_required) return showMfa(r.mfa_token);
      afterLogin(r);
    } catch (e) {
      err.show(describeError(e));
      if (e.code === "email_not_verified") {
        extra.append(el("p", {}, el("button", { class: "btn small", type: "button", onclick: async () => {
          await post("/api/v1/auth/resend-verification", { email: fd.get("email") }).catch(() => {});
          err.show(t("auth.resent"));
        } }, t("auth.resend"))));
      }
    }
  });

  const wrap = el("div", {}, form,
    el("div", { class: "auth-links" },
      el("a", { href: "#/forgot" }, t("auth.forgot")),
      meta.signup_enabled ? el("a", { href: "#/register" }, t("auth.no_account")) : null));

  function showMfa(mfaToken) {
    const err2 = errorBox();
    const code = input("text", "code", { inputmode: "numeric", autocomplete: "one-time-code", maxlength: "20", autofocus: true });
    const f = submitting(el("form", {}, el("p", { class: "muted" }, t("auth.mfa_hint")), err2, field(t("auth.mfa_code"), code),
      el("button", { class: "btn primary", type: "submit" }, t("auth.mfa_btn"))), async (fd) => {
      err2.hide();
      try {
        afterLogin(await post("/api/v1/auth/login/mfa", { mfa_token: mfaToken, code: fd.get("code") }));
      } catch (e) {
        err2.show(describeError(e));
        if (e.code === "mfa_session_expired") setTimeout(() => rerender(), 1500);
      }
    });
    clear(wrap, el("h2", {}, t("auth.mfa_title")), f);
    code.focus();
  }
  return authCard(t("auth.login_title"), wrap, rerender);
}

// ---------------------------------------------------------------------- Registrieren
export function viewRegister(rerender) {
  const err = errorBox();
  const pw = input("password", "password", { autocomplete: "new-password", minlength: String(meta.password_min_length), maxlength: "128" });
  const form = submitting(el("form", {},
    el("p", { class: "muted" }, t("auth.register_lead")), err,
    field(t("auth.org_name"), input("text", "org_name", { maxlength: "100", autocomplete: "organization", autofocus: true })),
    field(t("auth.your_name"), input("text", "name", { maxlength: "100", autocomplete: "name" })),
    field(t("c.email"), input("email", "email", { autocomplete: "email", maxlength: "254" })),
    field(t("c.password"), pw, t("auth.pw_hint", { n: meta.password_min_length })), passwordMeter(pw, meta.password_min_length),
    el("div", { class: "field" }, el("label", { class: "check" }, el("input", { type: "checkbox", name: "terms", required: true }),
      el("span", {}, t("auth.terms"), " ", el("a", { href: "/#privacy", target: "_blank", rel: "noopener" }, t("l.privacy"))))),
    el("button", { class: "btn primary", type: "submit" }, t("auth.register_btn"))), async (fd) => {
    err.hide();
    try {
      await post("/api/v1/auth/register", { org_name: fd.get("org_name"), name: fd.get("name"), email: fd.get("email"),
        password: fd.get("password"), accept_terms: fd.get("terms") === "on", locale: document.documentElement.lang || "de" });
      go("/check-email");
    } catch (e) { err.show(describeError(e)); }
  });
  return authCard(t("auth.register_title"), el("div", {}, form,
    el("div", { class: "auth-links" }, el("a", { href: "#/login" }, t("auth.have_account")))), rerender);
}

export function viewCheckEmail(rerender) {
  return authCard(t("auth.check_title"), el("div", {}, el("p", {}, t("auth.check_text")),
    el("a", { class: "btn", href: "#/login" }, t("auth.login_btn"))), rerender);
}

export function viewVerify(params, rerender) {
  const token = takeToken(params, "verify");
  keepToken("verify", token);
  const body = el("div", {}, el("p", {}, t("c.loading")));
  post("/api/v1/auth/verify-email", { token }).then(() => {
    keepToken("verify", null);
    clear(body, el("div", { class: "alert-box ok", role: "status" }, t("auth.verify_ok")), el("a", { class: "btn primary", href: "#/login" }, t("auth.login_btn")));
  }).catch((e) => clear(body, el("div", { class: "alert-box error", role: "alert" }, describeError(e)), el("a", { class: "btn", href: "#/login" }, t("auth.login_btn"))));
  return authCard(t("auth.verify_title"), body, rerender);
}

export function viewForgot(rerender) {
  const msg = el("div", { role: "status" });
  const form = submitting(el("form", {}, msg, field(t("c.email"), input("email", "email", { autocomplete: "email", autofocus: true })),
    el("button", { class: "btn primary", type: "submit" }, t("auth.forgot_btn"))), async (fd) => {
    try {
      await post("/api/v1/auth/password/forgot", { email: fd.get("email") });
      clear(msg, el("div", { class: "alert-box ok" }, t("auth.forgot_sent")));
    } catch (e) { clear(msg, el("div", { class: "alert-box error" }, describeError(e))); }
  });
  return authCard(t("auth.forgot_title"), el("div", {}, form, el("div", { class: "auth-links" }, el("a", { href: "#/login" }, t("c.back")))), rerender);
}

export function viewReset(params, rerender) {
  const token = takeToken(params, "reset");
  keepToken("reset", token);
  const err = errorBox();
  const pw = input("password", "password", { autocomplete: "new-password", maxlength: "128", autofocus: true });
  const body = el("div");
  const form = submitting(el("form", {}, err, field(t("auth.new_password"), pw, t("auth.pw_hint", { n: meta.password_min_length })),
    passwordMeter(pw, meta.password_min_length), el("button", { class: "btn primary", type: "submit" }, t("auth.reset_btn"))), async (fd) => {
    err.hide();
    try {
      await post("/api/v1/auth/password/reset", { token, password: fd.get("password") });
      keepToken("reset", null);
      clear(body, el("div", { class: "alert-box ok", role: "status" }, t("auth.reset_done")), el("a", { class: "btn primary", href: "#/login" }, t("auth.login_btn")));
    } catch (e) { err.show(describeError(e)); }
  });
  body.append(form);
  return authCard(t("auth.reset_title"), body, rerender);
}

export function viewInvite(params, rerender) {
  const token = takeToken(params, "invite");
  keepToken("invite", token);
  const body = el("div", {}, el("p", {}, t("c.loading")));
  post("/api/v1/auth/invite/info", { token }).then((info) => {
    const err = errorBox();
    const pw = input("password", "password", { autocomplete: "new-password", maxlength: "128" });
    const form = submitting(el("form", {},
      el("p", {}, t("auth.invite_text", { org: info.org_name, role: t("role." + info.role), email: info.email })), err,
      el("input", { type: "email", autocomplete: "username", value: info.email, hidden: true, readonly: true }),
      field(t("auth.your_name"), input("text", "name", { maxlength: "100", autocomplete: "name", autofocus: true })),
      field(t("c.password"), pw, t("auth.pw_hint", { n: meta.password_min_length })), passwordMeter(pw, meta.password_min_length),
      el("button", { class: "btn primary", type: "submit" }, t("auth.invite_btn"))), async (fd) => {
      err.hide();
      try {
        const me = await post("/api/v1/auth/invite/accept", { token, name: fd.get("name"), password: fd.get("password") });
        keepToken("invite", null);
        afterLogin(me);
      } catch (e) { err.show(describeError(e)); }
    });
    clear(body, form);
  }).catch((e) => clear(body, el("div", { class: "alert-box error", role: "alert" }, describeError(e))));
  return authCard(t("auth.invite_title"), body, rerender);
}
