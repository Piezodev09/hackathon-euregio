// Anlagen: mehrere Stellplätze nebeneinander – Übersicht „3 von 10 frei“, Großanzeige, Warteliste.
import { get, post, patch, del, describeError } from "./api.js";
import { t } from "./i18n.js";
import { el, clear, field, toast, confirmDialog, copyText, icon } from "./ui.js";
import { state, can, every } from "./state.js";
import { qrData, printSticker } from "./views-station.js";

const errorCard = (e) => el("div", { class: "alert-box error", role: "alert" }, describeError(e));
const CAT_STATE = { available: "free", occupied: "occupied", reserved: "reserved", closed: "unknown", unknown: "unknown" };

/** Kachel für die Übersicht: „3 von 10 frei“ (unbekannt zählt nie als frei). */
export function siteTile(s) {
  const st = s.free > 0 ? "free" : s.counts.unknown === s.total ? "unknown" : "occupied";
  return el("a", { class: `card site-kpi site-kpi--${st}`, href: "#/sites" },
    el("span", { class: "site-kpi__num" }, `${s.free}`, el("span", { class: "site-kpi__of" }, ` / ${s.total}`)),
    el("span", { class: "site-kpi__label" }, t("si.free_of", { name: s.name })),
    s.waiting ? el("span", { class: "badge warn" }, t("si.waiting", { n: s.waiting })) : null,
    s.simulated ? el("span", { class: "badge" }, "SIMULATION") : null);
}

export function viewSites() {
  const box = el("div", { class: "page-stack" }, el("p", {}, t("c.loading")));
  const plan = state.me.tenant.plan;
  let stations = [];

  const load = async () => {
    try {
      const [{ sites }, st] = await Promise.all([get("/api/v1/sites"), get("/api/v1/stations")]);
      stations = st.stations;
      const assigned = new Set(sites.flatMap((s) => s.stalls.map((x) => x.station_id)));
      clear(box,
        sites.length ? sites.map((s) => siteCard(s)) : el("section", { class: "card" }, el("p", { class: "muted" }, t("si.none"))),
        stations.some((x) => !assigned.has(x.id)) && sites.length
          ? el("p", { class: "small muted" }, t("si.unassigned", { n: stations.filter((x) => !assigned.has(x.id)).length })) : null,
        can("admin") ? createForm() : null);
    } catch (e) { clear(box, errorCard(e)); }
  };

  function stationPicker(selected) {
    const boxes = stations.map((s) => ({ s, box: el("input", { type: "checkbox", value: s.id, checked: selected.has(s.id) }) }));
    return { boxes, node: el("fieldset", { class: "field" }, el("legend", {}, t("si.stalls")),
      el("div", { class: "grid cols-3 checks" }, boxes.map(({ s, box }) => el("label", { class: "check" }, box, el("span", {}, s.name))))) };
  }

  function createForm() {
    const name = el("input", { type: "text", required: true, maxlength: "100", placeholder: t("si.name_ph") });
    const loc = el("input", { type: "text", maxlength: "200" });
    const picker = stationPicker(new Set());
    const form = el("form", { class: "card" }, el("h2", {}, "+ ", t("si.create")), el("p", { class: "muted" }, t("si.intro")),
      el("div", { class: "grid cols-2" }, field(t("si.name"), name), field(t("ss.location"), loc)), picker.node,
      el("button", { class: "btn primary", type: "submit" }, t("si.create")));
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await post("/api/v1/sites", { name: name.value, location: loc.value, station_ids: picker.boxes.filter((x) => x.box.checked).map((x) => x.s.id) });
        toast(t("c.saved")); load();
      } catch (e) { toast(describeError(e), "error"); }
    });
    return form;
  }

  function siteCard(s) {
    const reveal = el("div");
    const showLink = (url) => {
      const qr = qrData(url);
      clear(reveal, el("div", { class: "alert-box info" }, el("p", {}, t("si.link_once")), el("p", { class: "secret-box mono" }, url),
        el("div", { class: "btn-row" }, el("button", { class: "btn small", type: "button", onclick: () => copyText(url) }, t("c.copy")),
          el("a", { class: "btn small", href: url, target: "_blank", rel: "noopener noreferrer" }, t("si.open")),
          qr ? el("button", { class: "btn small", type: "button", onclick: () => printSticker(s.name, qr, t("si.sticker")) }, t("sv.print")) : null)));
    };
    const tiles = el("ul", { class: "site-mini" }, s.stalls.map((x) => el("li", { class: "site-mini__tile", "data-state": CAT_STATE[x.category] },
      icon(CAT_STATE[x.category]), el("strong", {}, x.name), el("span", {}, t("si.cat_" + x.category)))));
    const admin = can("admin");
    const actions = [];
    if (admin) {
      actions.push(el("button", { class: "btn", type: "button", disabled: !plan.public_display, onclick: async () => {
        if (s.display_enabled && !(await confirmDialog(t("si.rotate_confirm")))) return;
        try { showLink((await post(`/api/v1/sites/${s.id}/display-link`)).url); load(); } catch (e) { toast(describeError(e), "error"); }
      } }, s.display_enabled ? t("si.rotate") : t("si.display")));
      actions.push(el("button", { class: "btn danger", type: "button", onclick: async () => {
        if (!(await confirmDialog(t("si.delete_confirm", { name: s.name }), { danger: true }))) return;
        try { await del(`/api/v1/sites/${s.id}`); load(); } catch (e) { toast(describeError(e), "error"); }
      } }, t("c.delete")));
    }
    // Einstellungen: Stellplätze, Warteliste
    let settings = null;
    if (admin) {
      const picker = stationPicker(new Set(s.stalls.map((x) => x.station_id)));
      const wl = el("input", { type: "checkbox", checked: s.waitlist_enabled, disabled: !plan.reservations });
      const hold = el("input", { type: "number", min: "5", max: "30", value: String(s.hold_minutes) });
      const form = el("form", {}, picker.node,
        el("div", { class: "field" }, el("label", { class: "check" }, wl, el("span", {}, t("si.waitlist_on"))),
          el("p", { class: "hint" }, plan.reservations ? t("si.waitlist_hint") : t("pk.feature_off", { f: t("feat.reservations") }))),
        field(t("si.hold"), hold, t("si.hold_hint")),
        el("button", { class: "btn primary", type: "submit" }, t("c.save")));
      form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        try {
          await patch(`/api/v1/sites/${s.id}`, { station_ids: picker.boxes.filter((x) => x.box.checked).map((x) => x.s.id),
            waitlist_enabled: wl.checked, hold_minutes: Number(hold.value) });
          toast(t("c.saved")); load();
        } catch (e) { toast(describeError(e), "error"); }
      });
      settings = el("details", {}, el("summary", {}, t("si.settings")), form);
    }
    return el("section", { class: "card" },
      el("div", { class: "btn-row site-head" }, el("h2", {}, s.name), siteTile(s)),
      s.location ? el("p", { class: "muted" }, s.location) : null,
      s.waitlist_enabled ? el("p", { class: "small" }, icon("bell"), " ", t("si.waitlist_state", { n: s.waiting, m: s.hold_minutes })) : null,
      s.total ? tiles : el("p", { class: "muted" }, t("si.empty")),
      el("div", { class: "btn-row" }, actions), reveal, settings);
  }

  // Aktualisieren, aber nicht während jemand ein Formular bearbeitet
  every(10000, () => {
    const a = document.activeElement;
    if (box.querySelector("details[open]") || (a && box.contains(a) && a.matches("input, select, textarea"))) return;
    load();
  });
  return el("div", {}, el("p", { class: "muted" }, t("si.hint")), box);
}
