// Statistiken: Auslastung, Check-ins, Heatmap Wochentag × Stunde, Parkdauer, Stellplätze, NFC-Leser.
// Diagramme als inline-SVG (CSP-konform, Farben über CSS-Klassen, Hell/Dunkel). Jedes Diagramm hat eine
// Textzusammenfassung (aria-label) und eine Tabelle mit denselben Werten. „Keine Daten“ wird nie als 0 % gezeigt.
import { get, describeError } from "./api.js";
import { getLang, t } from "./i18n.js";
import { el, clear, fmtCents, fmtDateTime, icon } from "./ui.js";

const NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, ...children) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) n.setAttribute(k, String(v));
  for (const c of children.flat()) if (c !== null && c !== undefined) n.append(typeof c === "string" ? document.createTextNode(c) : c);
  return n;
}

const errorCard = (e) => el("div", { class: "alert-box error", role: "alert" }, describeError(e));
const th = (...hs) => el("thead", {}, el("tr", {}, hs.map((h) => el("th", { scope: "col" }, h))));
const pct = (v) => (v === null || v === undefined ? t("sx.no_data") : `${Math.round(v * 100)} %`);
const dayLabel = (iso, long = false) => new Date(iso + "T12:00:00").toLocaleDateString(getLang(),
  long ? { weekday: "short", day: "2-digit", month: "2-digit" } : { day: "2-digit", month: "2-digit" });
const WD = () => [0, 1, 2, 3, 4, 5, 6].map((i) => new Date(2023, 10, 13 + i).toLocaleDateString(getLang(), { weekday: "short" }));
const minutes = (m) => (m === null || m === undefined ? "–" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`);

function tile(value, label, hint) {
  return el("div", { class: "kpi-tile" }, el("span", { class: "value" }, String(value)), el("span", { class: "label" }, label),
    hint ? el("span", { class: "small muted" }, hint) : null);
}

function dataTable(caption, head, rows) {
  return el("details", {}, el("summary", {}, t("sx.as_table")),
    el("div", { class: "table-wrap" }, el("table", {}, el("caption", { class: "visually-hidden" }, caption), th(...head),
      el("tbody", {}, rows.map((r) => el("tr", {}, r.map((c) => el("td", {}, String(c)))))))));
}

// Liniendiagramm Auslastung je Tag; Tage ohne Daten als schraffierte Lücke, nicht als 0 %
function occupancyChart(series) {
  const W = 640, H = 220, L = 44, R = 12, T = 12, B = 30;
  const n = series.length, w = (W - L - R) / n;
  const x = (i) => L + w * i + w / 2;
  const y = (v) => T + (1 - v) * (H - T - B);
  const g = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-labelledby": "sx-occ-desc" });
  g.append(svg("desc", { id: "sx-occ-desc" }, occSummary(series)),
    svg("defs", {}, svg("pattern", { id: "sx-hatch", width: 8, height: 8, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
      svg("rect", { width: 8, height: 8, class: "hatch-bg" }), svg("line", { x1: 0, y1: 0, x2: 0, y2: 8, class: "hatch-line" }))));
  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    g.append(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid-line" }),
      svg("text", { x: L - 6, y: y(v) + 4, class: "axis", "text-anchor": "end" }, `${v * 100}%`));
  }
  series.forEach((d, i) => {
    if (d.occupancy === null) g.append(svg("rect", { x: L + w * i, y: T, width: w, height: H - T - B, class: "nodata" }));
  });
  let path = "";
  series.forEach((d, i) => {
    if (d.occupancy === null) { path += " "; return; }
    const prev = i > 0 && series[i - 1].occupancy !== null;
    path += `${prev ? "L" : "M"}${x(i).toFixed(1)},${y(d.occupancy).toFixed(1)}`;
  });
  g.append(svg("path", { d: path.trim().replace(/\s+/g, " "), class: "line" }));
  series.forEach((d, i) => {
    if (d.occupancy !== null) g.append(svg("circle", { cx: x(i), cy: y(d.occupancy), r: n > 40 ? 2 : 3.5, class: "dot" },
      svg("title", {}, `${dayLabel(d.day, true)}: ${pct(d.occupancy)}`)));
  });
  const step = Math.ceil(n / 8);
  series.forEach((d, i) => { if (i % step === 0 || i === n - 1) g.append(svg("text", { x: x(i), y: H - 8, class: "axis", "text-anchor": "middle" }, dayLabel(d.day))); });
  return g;
}

function occSummary(series) {
  const known = series.filter((d) => d.occupancy !== null);
  if (!known.length) return t("sx.occ_none");
  const max = known.reduce((a, b) => (b.occupancy > a.occupancy ? b : a));
  return t("sx.occ_desc", { n: known.length, m: series.length, day: dayLabel(max.day, true), v: pct(max.occupancy) });
}

// Säulen je Tag (Check-ins)
function barChart(series, key, label) {
  const W = 640, H = 180, L = 36, R = 12, T = 12, B = 30;
  const n = series.length, w = (W - L - R) / n;
  const max = Math.max(1, ...series.map((d) => d[key]));
  const nice = max <= 5 ? max : Math.ceil(max / 5) * 5;
  const y = (v) => T + (1 - v / nice) * (H - T - B);
  const total = series.reduce((a, d) => a + d[key], 0);
  const g = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": `${label}: ${total}` });
  for (const v of [0, nice / 2, nice]) {
    g.append(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid-line" }),
      svg("text", { x: L - 6, y: y(v) + 4, class: "axis", "text-anchor": "end" }, String(Math.round(v))));
  }
  series.forEach((d, i) => {
    const h = y(0) - y(d[key]);
    if (d[key] > 0) g.append(svg("rect", { x: L + w * i + w * 0.15, y: y(d[key]), width: w * 0.7, height: h, class: "bar", rx: 2 },
      svg("title", {}, `${dayLabel(d.day, true)}: ${d[key]}`)));
  });
  const step = Math.ceil(n / 8);
  series.forEach((d, i) => { if (i % step === 0 || i === n - 1) g.append(svg("text", { x: L + w * i + w / 2, y: H - 8, class: "axis", "text-anchor": "middle" }, dayLabel(d.day))); });
  return g;
}

// Heatmap Wochentag × Stunde; Stufen über CSS-Klassen, Wert zusätzlich als Text im Tooltip und in der Tabelle
function heatmap(cells) {
  const wd = WD();
  const level = (v) => (v === null ? "nodata" : `q${Math.min(4, Math.floor(v * 5))}`);
  const grid = el("div", { class: "heat", role: "img", "aria-label": t("sx.heat_desc") },
    el("span", { class: "heat-corner" }),
    [...Array(24).keys()].map((h) => el("span", { class: "heat-h" }, h % 3 === 0 ? String(h) : "")),
    wd.map((name, w) => [el("span", { class: "heat-d" }, name),
      cells[w].map((c, h) => el("span", { class: `heat-c ${level(c.occupancy)}`,
        title: `${name} ${h}–${h + 1} Uhr: ${pct(c.occupancy)} · ${t("sx.checkins")}: ${c.checkins}` }))]));
  const legend = el("div", { class: "heat-legend small" },
    el("span", {}, el("span", { class: "heat-c nodata" }), " ", t("sx.no_data")),
    ["0–19 %", "20–39 %", "40–59 %", "60–79 %", "80–100 %"].map((l, i) => el("span", {}, el("span", { class: `heat-c q${i}` }), " ", l)));
  return [el("div", { class: "heat-scroll" }, grid), legend];
}

function durationBars(bins) {
  const max = Math.max(1, ...bins.map((b) => b.count));
  return el("div", { class: "hbars" }, bins.map((b) => {
    const bar = el("span", { class: "hbar" });
    bar.style.width = `${Math.round((b.count / max) * 100)}%`;
    return el("div", { class: "hbar-row" }, el("span", { class: "hbar-l" }, b.label), el("span", { class: "hbar-track" }, bar),
      el("span", { class: "hbar-v" }, String(b.count)));
  }));
}

const RESULT_KEYS = ["checked_in", "checked_out", "unknown_card", "blocked", "occupied_by_other", "open_elsewhere", "expired",
  "feature_disabled", "maintenance", "closed", "reserved", "insufficient_balance", "learned"];

export function viewStats() {
  const out = el("div", { class: "page-stack" }, el("p", {}, t("c.loading")));
  let days = 30;
  let station = "";
  const seg = el("div", { class: "seg", role: "group", "aria-label": t("sx.range") });
  const sel = el("select", { "aria-label": t("ev.station") }, el("option", { value: "" }, t("sx.all_stalls")));
  const renderSeg = () => clear(seg, [7, 30, 90].map((d) => el("button", { type: "button", "aria-pressed": String(d === days),
    onclick: () => { days = d; renderSeg(); load(); } }, t("sx.days", { n: d }))));
  sel.addEventListener("change", () => { station = sel.value; load(); });
  renderSeg();
  get("/api/v1/stations").then(({ stations }) => stations.forEach((s) => sel.append(el("option", { value: s.id }, s.name)))).catch(() => {});

  async function load() {
    try {
      const s = await get(`/api/v1/stats?days=${days}${station ? `&station_id=${encodeURIComponent(station)}` : ""}`);
      const T = s.totals;
      const wd = WD();
      const blocks = [];
      if (s.preview) {
        blocks.push(el("div", { class: "alert-box info upsell" }, icon("info"), el("div", {}, el("p", {}, el("strong", {}, t("sx.preview"))),
          el("p", { class: "small" }, t("sx.preview_text")), el("a", { class: "btn small primary", href: "#/billing" }, t("up.cta")))));
      }
      if (s.simulated) blocks.push(el("div", { class: "alert-box warn" }, el("strong", {}, "SIMULATION"), " – ", t("sx.simulated")));
      if (days > s.retention_days) blocks.push(el("p", { class: "small muted" }, t("sx.retention", { n: s.retention_days })));
      blocks.push(el("p", { class: "muted" }, t("sx.period", { a: dayLabel(s.from, true), b: dayLabel(s.to, true) })));
      blocks.push(el("div", { class: "grid cols-5" },
        tile(pct(T.occupancy), t("sx.occupancy"), t("sx.occupancy_hint")),
        tile(pct(T.data_coverage), t("sx.coverage"), t("sx.coverage_hint")),
        tile(T.checkins, t("sx.checkins")),
        tile(fmtCents(T.fees_cents), t("sx.fees")),
        tile(minutes(T.median_minutes), t("sx.median")),
        tile(T.peak ? `${wd[T.peak.weekday]} ${T.peak.hour}:00` : "–", t("sx.peak"), T.peak ? pct(T.peak.occupancy) : null),
        tile(T.tap_success === null ? "–" : pct(T.tap_success), t("sx.tap_success"), t("sx.taps_n", { n: T.taps })),
        tile(T.unknown_cards, t("sx.unknown_cards")),
        tile(T.warnings, t("sx.warnings")),
        tile(T.reservations, t("sx.reservations"))));

      blocks.push(el("section", { class: "card" }, el("h2", {}, t("sx.occ_title")), el("p", { class: "small muted" }, t("sx.occ_intro")),
        occupancyChart(s.series),
        el("div", { class: "chart-legend small" }, el("span", {}, el("span", { class: "key line" }), " ", t("sx.occupancy")),
          el("span", {}, el("span", { class: "key nodata" }), " ", t("sx.no_data"))),
        dataTable(t("sx.occ_title"), [t("sx.day"), t("sx.occupancy"), t("sx.coverage"), t("sx.checkins"), t("sx.fees"), t("sx.taps"), t("sx.warnings")],
          s.series.map((d) => [dayLabel(d.day, true), pct(d.occupancy), pct(d.data_coverage), d.checkins, fmtCents(d.fees_cents), d.taps, d.warnings]))));

      blocks.push(el("div", { class: "grid cols-2" },
        el("section", { class: "card" }, el("h2", {}, t("sx.checkins_title")), barChart(s.series, "checkins", t("sx.checkins"))),
        el("section", { class: "card" }, el("h2", {}, t("sx.duration_title")),
          el("p", { class: "small muted" }, t("sx.median"), ": ", minutes(T.median_minutes)), durationBars(s.durations))));

      blocks.push(el("section", { class: "card" }, el("h2", {}, t("sx.heat_title")), el("p", { class: "small muted" }, t("sx.heat_intro")),
        ...heatmap(s.heatmap),
        dataTable(t("sx.heat_title"), [t("sx.weekday"), ...[...Array(24).keys()].map((h) => `${h}`)],
          s.heatmap.map((row, w) => [wd[w], ...row.map((c) => (c.occupancy === null ? "–" : Math.round(c.occupancy * 100)))]))));

      if (!station) {
        blocks.push(el("section", { class: "card" }, el("h2", {}, t("sx.by_stall")), el("div", { class: "table-wrap" }, el("table", {},
          th(t("ev.station"), t("sx.occupancy"), t("sx.coverage"), t("sx.checkins"), t("sx.fees"), t("sx.median")),
          el("tbody", {}, s.stations.map((r) => el("tr", {},
            el("td", {}, el("a", { href: `#/stations/${r.station_id}` }, r.name), r.simulated ? [" ", el("span", { class: "badge" }, "SIMULATION")] : null),
            el("td", {}, pct(r.occupancy)), el("td", {}, pct(r.data_coverage)), el("td", {}, String(r.checkins)),
            el("td", {}, fmtCents(r.fees_cents)), el("td", {}, minutes(r.median_minutes)))))))));
      }

      const results = RESULT_KEYS.filter((k) => s.nfc.by_result[k]);
      blocks.push(el("section", { class: "card" }, el("h2", {}, icon("card"), " ", t("sx.nfc_title")),
        T.taps ? el("div", { class: "grid cols-2" },
          el("div", { class: "table-wrap" }, el("table", {}, th(t("sx.result"), t("sx.count")),
            el("tbody", {}, results.map((k) => el("tr", {}, el("td", {}, t("tap." + k)), el("td", {}, String(s.nfc.by_result[k]))))))),
          el("div", { class: "table-wrap" }, el("table", {}, th(t("sx.reader"), t("sx.taps"), t("sx.tap_success"), t("sx.last_tap")),
            el("tbody", {}, s.nfc.readers.map((r) => el("tr", {}, el("td", {}, r.name), el("td", {}, String(r.taps)),
              el("td", {}, pct(r.success)), el("td", {}, fmtDateTime(r.last_at))))))))
          : el("p", { class: "muted" }, t("sx.no_taps"))));
      clear(out, blocks);
    } catch (e) { clear(out, errorCard(e)); }
  }
  load();
  return el("div", {}, el("div", { class: "btn-row toolbar" }, seg, sel), out);
}
