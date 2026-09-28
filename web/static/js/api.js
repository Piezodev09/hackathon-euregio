// API-Client: gleiche Herkunft, Session-Cookie (HttpOnly) + CSRF-Header, übersetzte Fehlermeldungen.
import { t } from "./i18n.js";

let csrfToken = null;
export const setCsrf = (v) => { csrfToken = v; };

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : detail?.code || "generic");
    this.status = status;
    this.detail = detail;
    this.code = typeof detail === "string" ? detail : detail?.code || "generic";
  }
  get message_() { return describeError(this); }
}

export function describeError(err) {
  if (!(err instanceof ApiError)) return t("err.network");
  const d = err.detail;
  if (err.code === "weak_password" && d?.problems) {
    return `${t("err.weak_password")} ${d.problems.map((p) => t("pw." + p)).join(", ")}`;
  }
  if (err.code === "plan_limit" && d) return t("err.plan_limit", { limit: d.limit, value: d.value });
  const key = "err." + err.code;
  const s = t(key);
  return s === key ? t("err.generic") : s;
}

let onAuthLost = null;
export const setAuthLostHandler = (fn) => { onAuthLost = fn; };

export async function api(method, path, body, { raw = false, headers = {} } = {}) {
  const h = { Accept: "application/json", ...headers };
  if (body !== undefined) h["Content-Type"] = "application/json";
  if (csrfToken && !["GET", "HEAD"].includes(method)) h["X-CSRF-Token"] = csrfToken;
  let res;
  try {
    res = await fetch(path, { method, headers: h, credentials: "same-origin", cache: "no-store",
      body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (_) {
    throw new ApiError(0, "network");
  }
  if (raw && res.ok) return res;
  let data = null;
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) {
    const err = new ApiError(res.status, data?.detail ?? "generic");
    if ((res.status === 401 && err.code === "not_authenticated") || err.code === "mfa_setup_required" || err.code === "tenant_suspended") {
      onAuthLost && onAuthLost(err);
    }
    throw err;
  }
  return data;
}

export const get = (p, o) => api("GET", p, undefined, o);
export const post = (p, b, o) => api("POST", p, b ?? {}, o);
export const patch = (p, b) => api("PATCH", p, b);
export const del = (p) => api("DELETE", p);
