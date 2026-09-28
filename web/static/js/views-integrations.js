// Integrations: read-only API keys, outgoing webhooks, Home Assistant (add-on / MQTT) and REST examples.
// Keys and webhook secrets are shown exactly once; the server stores only a hash / encrypted value.
import { get, post, patch, del, describeError } from "./api.js";
import { t } from "./i18n.js";
import { el, clear, field, toast, confirmDialog, fmtDateTime, copyText } from "./ui.js";
import { can } from "./state.js";

const errorCard = (e) => el("div", { class: "alert-box error", role: "alert" }, describeError(e));
const th = (...hs) => el("thead", {}, el("tr", {}, hs.map((h) => el("th", { scope: "col" }, h))));
const KIND_LABEL = { generic: "int.kind_generic", slack: "Slack", teams: "Microsoft Teams", discord: "Discord" };
const EVENT_LABEL = { alert: "ev.k_unusual_movement", sensor_fault: "ev.k_sensor_fault",
  gateway_offline: "ev.k_gateway_offline", gateway_online: "ev.k_gateway_online" };
const kindLabel = (k) => (KIND_LABEL[k].startsWith("int.") ? t(KIND_LABEL[k]) : KIND_LABEL[k]);

function onSubmit(form, fn) {
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    if (btn) btn.disabled = true;
    try { await fn(); } catch (e) { toast(describeError(e), "error"); } finally { if (btn) btn.disabled = false; }
  });
  return form;
}

// Copyable code (commands, URLs, config snippets).
function codeRow(text, label) {
  return el("div", { class: "field" }, label ? el("div", { class: "small muted" }, label) : null,
    el("div", { class: "btn-row" }, el("pre", { class: "secret-box mono small cmd" }, text),
      el("button", { class: "btn small", type: "button", "aria-label": `${t("c.copy")}: ${label || text.slice(0, 40)}`,
        onclick: () => copyText(text) }, t("c.copy"))));
}

// A credential that is shown exactly once.
function secretOnce(title, secret, hint, extra) {
  return el("div", { class: "alert-box warn", role: "status" },
    el("h3", {}, title), el("p", {}, t("int.once")),
    el("div", { class: "btn-row" }, el("p", { class: "secret-box mono small cmd" }, secret),
      el("button", { class: "btn small", type: "button", onclick: () => copyText(secret) }, t("c.copy"))),
    hint ? el("p", { class: "small" }, hint) : null, extra || null);
}

// ---------------------------------------------------------------------- API keys
function apiKeysCard(info) {
  const list = el("div", {}, t("c.loading"));
  const reveal = el("div");
  const load = async () => {
    try {
      const { api_keys: keys } = await get("/api/v1/integrations/api-keys");
      clear(list, keys.length ? el("div", { class: "table-wrap" }, el("table", {},
        th(t("c.name"), t("int.key"), t("c.created"), t("int.last_used"), t("c.status"), t("c.actions")),
        el("tbody", {}, keys.map((k) => el("tr", {},
          el("td", {}, k.name), el("td", { class: "mono small" }, k.prefix + "…"),
          el("td", { class: "small" }, fmtDateTime(k.created_at), el("div", { class: "muted" }, k.created_by || "")),
          el("td", { class: "small" }, fmtDateTime(k.last_used_at)),
          el("td", {}, k.revoked_at ? el("span", { class: "badge err" }, t("int.revoked")) : el("span", { class: "badge ok" }, t("int.active"))),
          el("td", {}, k.revoked_at ? "–" : el("button", { class: "btn small danger", type: "button", onclick: async () => {
            if (!(await confirmDialog(t("int.revoke_confirm", { name: k.name }), { danger: true }))) return;
            try { await del(`/api/v1/integrations/api-keys/${k.id}`); toast(t("int.revoked")); load(); }
            catch (e) { toast(describeError(e), "error"); }
          } }, t("int.revoke")))))))) : el("p", { class: "muted" }, t("int.keys_none")));
    } catch (e) { clear(list, errorCard(e)); }
  };
  const name = el("input", { type: "text", required: true, maxlength: "100", placeholder: t("int.key_name_ph") });
  const form = onSubmit(el("form", { class: "btn-row form-inline" }, field(t("int.key_name"), name),
    el("button", { class: "btn primary", type: "submit" }, t("int.key_create"))), async () => {
    const k = await post("/api/v1/integrations/api-keys", { name: name.value });
    clear(reveal, secretOnce(t("int.key_created", { name: k.name }), k.key, t("int.key_use"),
      codeRow(`curl${info.ca_fingerprint ? " --cacert ca.crt" : ""} -H "Authorization: Bearer ${k.key}" ${info.api_base}/stations`)));
    name.value = "";
    load();
  });
  load();
  return el("section", { class: "card", "aria-labelledby": "int-keys" },
    el("h2", { id: "int-keys" }, t("int.keys")), el("p", { class: "muted" }, t("int.keys_hint")),
    reveal, list, form);
}

// ---------------------------------------------------------------------- webhooks
function deliveryStatus(w) {
  if (!w.last_attempt_at) return el("span", { class: "small muted" }, t("int.never_sent"));
  const ok = w.last_status === "ok";
  return el("div", { class: "small" },
    el("span", { class: ok ? "badge ok" : "badge err" }, ok ? "✓ " + t("int.delivered") : "✗ " + t("int.failed")),
    el("div", { class: "muted" }, fmtDateTime(w.last_attempt_at)),
    !ok && w.last_error ? el("div", {}, targetReason(w.last_error)) : null);
}

function targetReason(code) {
  const key = "int.err_" + code;
  const s = t(key);
  return s === key ? code : s;
}

function webhooksCard(info) {
  const list = el("div", {}, t("c.loading"));
  const reveal = el("div");
  const formErr = el("div", { role: "alert" });
  let meta = { kinds: Object.keys(KIND_LABEL), event_types: Object.keys(EVENT_LABEL), allow_private: info.webhook_allow_private };

  const load = async () => {
    try {
      const r = await get("/api/v1/integrations/webhooks");
      meta = r;
      clear(list, r.webhooks.length ? el("div", { class: "table-wrap" }, el("table", {},
        th(t("c.name"), t("int.target"), t("int.events"), t("int.last_delivery"), t("int.enabled"), t("c.actions")),
        el("tbody", {}, r.webhooks.map((w) => {
          const toggle = el("input", { type: "checkbox", checked: w.enabled, "aria-label": `${t("int.enabled")}: ${w.name}` });
          toggle.addEventListener("change", async () => {
            try { await patch(`/api/v1/integrations/webhooks/${w.id}`, { enabled: toggle.checked }); toast(t("c.saved")); }
            catch (e) { toggle.checked = !toggle.checked; toast(describeError(e), "error"); }
          });
          return el("tr", {},
            el("td", {}, el("strong", {}, w.name)),
            el("td", { class: "small" }, el("span", { class: "badge" }, kindLabel(w.kind)), el("div", { class: "mono url-cell" }, w.url)),
            el("td", { class: "small" }, w.events.map((ev) => t(EVENT_LABEL[ev])).join(", ")),
            el("td", {}, deliveryStatus(w)),
            el("td", {}, toggle),
            el("td", {}, el("div", { class: "btn-row" },
              el("button", { class: "btn small", type: "button", onclick: async (ev) => {
                ev.currentTarget.setAttribute("aria-busy", "true");
                const btn = ev.currentTarget;
                try {
                  const res = await post(`/api/v1/integrations/webhooks/${w.id}/test`);
                  toast(res.ok ? t("int.test_ok", { r: res.result }) : t("int.test_failed", { r: targetReason(res.result) }), res.ok ? "ok" : "error");
                } catch (e) { toast(describeError(e), "error"); }
                btn.removeAttribute("aria-busy");
                load();
              } }, t("int.test")),
              el("button", { class: "btn small danger", type: "button", onclick: async () => {
                if (!(await confirmDialog(t("int.wh_delete_confirm", { name: w.name }), { danger: true }))) return;
                try { await del(`/api/v1/integrations/webhooks/${w.id}`); load(); } catch (e) { toast(describeError(e), "error"); }
              } }, t("c.delete")))));
        })))) : el("p", { class: "muted" }, t("int.wh_none")));
    } catch (e) { clear(list, errorCard(e)); }
  };

  const name = el("input", { type: "text", required: true, maxlength: "100", placeholder: t("int.wh_name_ph") });
  const url = el("input", { type: "url", required: true, maxlength: "1000", placeholder: "https://hooks.slack.com/services/…", inputmode: "url", spellcheck: "false" });
  const kind = el("select", {}, Object.keys(KIND_LABEL).map((k) => el("option", { value: k }, kindLabel(k))));
  const boxes = Object.keys(EVENT_LABEL).map((ev) => el("input", { type: "checkbox", value: ev, checked: ev === "alert" || ev === "gateway_offline" }));
  const events = el("fieldset", { class: "field" }, el("legend", {}, t("int.events")),
    el("div", { class: "grid cols-2 tight" }, boxes.map((b) => el("label", { class: "check" }, b, el("span", {}, t(EVENT_LABEL[b.value]))))));
  const form = onSubmit(el("form", {}, el("h3", {}, t("int.wh_add")), formErr,
    el("div", { class: "grid cols-2" }, field(t("c.name"), name), field(t("int.wh_type"), kind)),
    field(t("int.wh_url"), url, t(info.webhook_allow_private ? "int.wh_url_lan" : "int.wh_url_public")),
    events, el("button", { class: "btn primary", type: "submit" }, t("int.wh_create"))), async () => {
    clear(formErr);
    url.removeAttribute("aria-invalid");
    const selected = boxes.filter((b) => b.checked).map((b) => b.value);
    if (!selected.length) { clear(formErr, el("div", { class: "alert-box error" }, t("int.wh_pick_event"))); return; }
    try {
      const w = await post("/api/v1/integrations/webhooks", { name: name.value, url: url.value.trim(), kind: kind.value, events: selected });
      clear(reveal, secretOnce(t("int.wh_created", { name: w.name }), w.secret, w.kind === "generic" ? t("int.wh_secret_hint") : t("int.wh_secret_chat"),
        w.kind === "generic" ? el("details", {}, el("summary", {}, t("int.wh_verify")), codeRow(VERIFY_SNIPPET)) : null));
      name.value = ""; url.value = "";
      load();
    } catch (e) {
      if (e.code === "webhook_target") {
        url.setAttribute("aria-invalid", "true");
        clear(formErr, el("div", { class: "alert-box error" }, targetReason(e.detail.reason)));
        url.focus();
      } else throw e;
    }
  });
  load();
  return el("section", { class: "card", "aria-labelledby": "int-hooks" },
    el("h2", { id: "int-hooks" }, t("int.webhooks")), el("p", { class: "muted" }, t("int.wh_hint")),
    reveal, list, el("div", { class: "subsection" }, form));
}

const VERIFY_SNIPPET = `# Python: verify X-BikeStation-Signature (t=<unix time>,v1=<hex HMAC-SHA256>)
import hashlib, hmac, time

def verify(secret: str, body: bytes, header: str, tolerance_s: int = 300) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    t = int(parts["t"])
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return abs(time.time() - t) <= tolerance_s and hmac.compare_digest(mac, parts["v1"])`;

// ---------------------------------------------------------------------- Home Assistant
function homeAssistantCard(info) {
  const fp = info.ca_fingerprint;
  return el("section", { class: "card", "aria-labelledby": "int-ha" },
    el("h2", { id: "int-ha" }, t("int.ha")), el("p", { class: "muted" }, t("int.ha_hint")),
    el("div", { class: "grid cols-2" },
      el("div", {},
        el("h3", {}, t("int.ha_addon")), el("p", { class: "small muted" }, t("int.ha_addon_when")),
        el("ol", { class: "steps" },
          el("li", {}, t("int.ha_step_repo"), codeRow(info.addon_repository)),
          el("li", {}, t("int.ha_step_install")),
          el("li", {}, t("int.ha_step_code"), " ", el("a", { href: "#/stations" }, t("int.ha_to_stations"))),
          el("li", {}, t("int.ha_step_config"), codeRow(info.platform_url, "platform_url"),
            fp ? codeRow(`SHA-256 ${fp}`, "ca_fingerprint") : null),
          el("li", {}, t("int.ha_step_mqtt")))),
      el("div", {},
        el("h3", {}, t("int.ha_pi")), el("p", { class: "small muted" }, t("int.ha_pi_when")),
        el("ol", { class: "steps" },
          el("li", {}, t("int.ha_pi_user")),
          el("li", {}, t("int.ha_pi_cmd"),
            codeRow("sudo apt install -y python3-paho-mqtt\n"
              + "sudo BIKE_MQTT_PASSWORD='…' bike-agent mqtt --mqtt-host 192.168.1.20 --mqtt-username bikestation\n"
              + "sudo systemctl restart bike-agent")),
          el("li", {}, t("int.ha_pi_done"))))),
    el("h3", {}, t("int.ha_entities")),
    el("ul", { class: "small" }, ["int.ha_e_space", "int.ha_e_alert", "int.ha_e_free", "int.ha_e_health"].map((k) => el("li", {}, t(k)))),
    el("p", { class: "small muted" }, t("int.ha_note")));
}

// ---------------------------------------------------------------------- REST
function restCard(info, stationId) {
  const cacert = info.ca_fingerprint ? " --cacert ca.crt" : "";
  const sid = stationId || "st_…";
  const endpoints = [
    ["GET /api/v1/stations", "int.ep_stations"],
    [`GET /api/v1/stations/${sid}/status`, "int.ep_status"],
    [`GET /api/v1/stations/${sid}/occupancy?hours=24`, "int.ep_occupancy"],
    [`GET /api/v1/stations/${sid}/occupancy/week`, "int.ep_week"],
    ["GET /api/v1/events?open_only=true", "int.ep_events"],
  ];
  return el("section", { class: "card", "aria-labelledby": "int-rest" },
    el("h2", { id: "int-rest" }, t("int.rest")), el("p", { class: "muted" }, t("int.rest_hint")),
    el("div", { class: "table-wrap" }, el("table", {}, th(t("int.endpoint"), t("int.returns")),
      el("tbody", {}, endpoints.map(([ep, k]) => el("tr", {}, el("td", { class: "mono small wrap-any" }, ep), el("td", { class: "small" }, t(k))))))),
    info.ca_fingerprint ? el("div", {}, el("h3", {}, t("int.rest_ca")), el("p", { class: "small" }, t("int.rest_ca_hint")),
      codeRow(`curl -fsSk -o ca.crt ${info.platform_url}/install/ca.crt\nopenssl x509 -in ca.crt -noout -fingerprint -sha256`),
      el("p", { class: "small mono" }, "SHA-256 ", info.ca_fingerprint)) : null,
    el("h3", {}, t("int.rest_example")),
    codeRow(`curl${cacert} -H "Authorization: Bearer bsk_…" \\\n  ${info.api_base}/stations/${sid}/status`),
    el("p", { class: "small muted" }, t("int.rest_limits")));
}

// ---------------------------------------------------------------------- page
export function viewIntegrations() {
  const node = el("div", {}, el("div", { class: "card" }, t("c.loading")));
  (async () => {
    try {
      const [info, stations] = await Promise.all([get("/api/v1/integrations/info"), get("/api/v1/stations")]);
      const sid = stations.stations[0]?.id;
      clear(node,
        can("admin") ? apiKeysCard(info) : null,
        can("admin") ? webhooksCard(info) : el("div", { class: "alert-box info" }, t("int.admin_only")),
        homeAssistantCard(info),
        restCard(info, sid));
    } catch (e) { clear(node, errorCard(e)); }
  })();
  return node;
}
