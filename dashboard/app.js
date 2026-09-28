// Dashboard der Smarten Radstation. Bewusst ohne Framework (Plan 3.1).
// Grundsatz: Bei fehlenden oder veralteten Daten NIE "frei" anzeigen.
(function () {
  "use strict";

  const params = new URLSearchParams(location.search);
  const STATION = params.get("station") || "demo-01";
  const STATUS_URL = `/api/v1/stations/${encodeURIComponent(STATION)}/status`;
  const SUMMARY_URL = "/api/v1/occupancy/summary?hours=12";
  const SYMBOL = { free: "✓", occupied: "■", unknown: "?" };

  let lang = pickLang();
  let pollMs = 2000;
  let staleAfterS = 30;
  let lastStatus = null;
  let lastOkAt = null; // Date.now() der letzten erfolgreichen Antwort
  let lastSummary = null;
  let lastEvents = null;

  function store(key, value) {
    try {
      if (value === undefined) return sessionStorage.getItem(key);
      if (value === null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } catch (_) { /* Speicher evtl. blockiert – dann eben ohne */ }
    return null;
  }

  function pickLang() {
    const q = params.get("lang");
    if (q && window.I18N[q]) return q;
    try {
      const saved = localStorage.getItem("lang");
      if (saved && window.I18N[saved]) return saved;
    } catch (_) {}
    const nav = (navigator.language || "de").slice(0, 2);
    return window.I18N[nav] ? nav : "de";
  }

  const t = (key, ...args) => {
    const v = window.I18N[lang][key] ?? window.I18N.de[key] ?? key;
    return typeof v === "function" ? v(...args) : v;
  };

  // Platzname in der gewählten Sprache (Kennung aus der Konfiguration).
  const slotName = (s) => t("slotName", s.slot_id);

  const fmtTime = (iso) =>
    iso ? new Date(iso).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–";

  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v;
      else if (v !== undefined && v !== null) e.setAttribute(k, v);
    }
    for (const c of children) if (c !== null && c !== undefined) e.append(c);
    return e;
  }

  // -------------------------------------------------------------- Sprache
  function applyLang() {
    document.documentElement.lang = lang;
    document.querySelectorAll("[data-i18n]").forEach((n) => (n.textContent = t(n.dataset.i18n)));
    document.querySelectorAll("[data-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
    document.title = t("title");
    render();
    renderSummary();
    renderEvents();
  }

  document.querySelectorAll("[data-lang]").forEach((b) =>
    b.addEventListener("click", () => {
      lang = b.dataset.lang;
      try { localStorage.setItem("lang", lang); } catch (_) {}
      applyLang();
    })
  );

  // -------------------------------------------------------------- Status
  async function poll() {
    try {
      const r = await fetch(STATUS_URL, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      lastStatus = await r.json();
      lastOkAt = Date.now();
      pollMs = Math.max(1000, (lastStatus.poll_interval_s || 2) * 1000);
      staleAfterS = lastStatus.stale_after_s || 30;
    } catch (_) {
      // Fehler wird unten sichtbar gemacht; kein stillschweigend eingefrorener Status.
    }
    render();
    setTimeout(poll, pollMs);
  }

  function effectiveSlots() {
    if (!lastStatus) return [];
    const ageS = lastOkAt ? (Date.now() - lastOkAt) / 1000 : Infinity;
    if (ageS > staleAfterS) {
      return lastStatus.slots.map((s) => ({ ...s, state: "unknown", unknown_reason: "connection", alert: s.alert }));
    }
    return lastStatus.slots;
  }

  function render() {
    const conn = document.getElementById("connection");
    const ageS = lastOkAt ? Math.round((Date.now() - lastOkAt) / 1000) : null;
    const connOk = ageS !== null && ageS * 1000 <= pollMs * 2 + 1500;
    const connStale = ageS === null || ageS > staleAfterS;

    conn.textContent = connOk ? t("connOk") : connStale ? t("connLostStale") : t("connLost", ageS);
    conn.className = connOk ? "ok" : "error";

    const slots = effectiveSlots();
    const free = slots.filter((s) => s.state === "free");
    const rec = connStale ? null : lastStatus && lastStatus.recommendation;
    const recSlot = slots.find((s) => s.slot_id === rec && s.state === "free");

    document.getElementById("free-count").textContent = t("freeCount", free.length, slots.length || "–");
    document.getElementById("recommendation").textContent = recSlot
      ? t("recommendation", slotName(recSlot))
      : t("noRecommendation");
    document.getElementById("updated").textContent = t("updated", fmtTime(lastStatus && lastStatus.server_time));
    document.getElementById("simulated").hidden = !(lastStatus && lastStatus.simulated_data);

    const list = document.getElementById("slots");
    list.replaceChildren(
      ...slots.map((s) => {
        const isRec = recSlot && s.slot_id === recSlot.slot_id;
        const reason = s.state === "unknown" && s.unknown_reason ? t("reason_" + s.unknown_reason) : null;
        return el(
          "li",
          { class: `slot ${s.state}${isRec ? " recommended" : ""}${s.alert ? " alerting" : ""}` },
          el("span", { class: "slot-label" }, slotName(s)),
          el("span", { class: "slot-state" },
            el("span", { class: "sym", "aria-hidden": "true" }, SYMBOL[s.state]),
            " ",
            t(s.state)
          ),
          reason ? el("span", { class: "slot-reason" }, reason) : null,
          isRec ? el("span", { class: "badge rec" }, "→ " + t("recommended")) : null,
          s.alert ? el("span", { class: "badge warn" }, "⚠ " + t("alertMovement")) : null,
          el("span", { class: "slot-time small" }, fmtTime(s.last_update))
        );
      })
    );

    const alerts = document.getElementById("alerts");
    const active = slots.filter((s) => s.alert);
    const text = active
      .map((s) => t("alertText", slotName(s), fmtTime(s.alert.occurred_at)) + (s.alert.simulated ? ` [${t("simulatedShort")}]` : ""))
      .join("\n");
    if (alerts.dataset.text !== text) {
      alerts.dataset.text = text; // nur bei Änderung neu setzen, damit Screenreader nicht dauernd vorlesen
      alerts.replaceChildren(...active.map((s, i) => el("p", { class: "alert" }, "⚠ " + text.split("\n")[i])));
    }

    if (lastStatus && lastStatus.ai) {
      const ai = lastStatus.ai;
      document.getElementById("ai-status").textContent =
        (ai.visible_detector === "ml" ? t("aiMl") : t("aiRule")) + " " + (ai.model_available ? t("aiModelOk") : t("aiModelOff"));
    }
  }

  // -------------------------------------------------------------- Auslastung
  async function pollSummary() {
    try {
      const r = await fetch(SUMMARY_URL, { cache: "no-store" });
      if (r.ok) lastSummary = await r.json();
    } catch (_) {}
    renderSummary();
    setTimeout(pollSummary, 60_000);
  }

  function renderSummary() {
    const table = document.getElementById("heatmap");
    const text = document.getElementById("usage-text");
    const src = document.getElementById("usage-source");
    if (!lastSummary || !lastStatus) {
      text.textContent = t("usageNone");
      return;
    }
    const slots = lastStatus.slots;
    // Stunden vor der ersten Messung weglassen.
    const first = lastSummary.buckets.findIndex((b) => b.avg_occupied_slots !== null);
    const buckets = first < 0 ? [] : lastSummary.buckets.slice(first);
    const hourOf = (iso) => String(new Date(iso).getHours()).padStart(2, "0");

    table.tHead.replaceChildren(
      el("tr", {}, el("th", { scope: "col" }, t("hour")), ...slots.map((s) => el("th", { scope: "col" }, slotName(s))))
    );
    table.tBodies[0].replaceChildren(
      ...buckets.map((b) =>
        el(
          "tr",
          {},
          el("th", { scope: "row" }, hourOf(b.hour_start)),
          ...slots.map((s) => {
            const v = b.occupancy[s.slot_id];
            if (v === null || v === undefined) return el("td", { class: "nodata" }, t("noData"));
            const pct = Math.round(v * 100);
            const level = Math.min(4, Math.floor(v * 5));
            return el("td", { class: `lvl${level}` }, `${pct} %`);
          })
        )
      )
    );

    const withData = buckets.filter((b) => b.avg_occupied_slots !== null);
    if (!withData.length) {
      text.textContent = t("usageNone");
    } else {
      const top = withData.reduce((a, b) => (b.avg_occupied_slots > a.avg_occupied_slots ? b : a));
      text.textContent = t("usageText", hourOf(top.hour_start), top.avg_occupied_slots.toLocaleString(lang));
    }
    src.textContent = lastSummary.contains_simulated ? t("usageSim") : lastSummary.contains_live ? t("usageLive") : "";
    src.className = lastSummary.contains_simulated ? "small badge sim" : "small";
  }

  // -------------------------------------------------------------- Verwaltung
  const tokenInput = document.getElementById("admin-token");
  const adminMsg = document.getElementById("admin-msg");

  async function loadEvents() {
    const token = store("adminToken");
    if (!token) return;
    try {
      const r = await fetch("/api/v1/events?include_shadow=true&limit=50", {
        headers: { Authorization: "Bearer " + token },
        cache: "no-store",
      });
      if (r.status === 401 || r.status === 403) {
        adminMsg.textContent = t("adminDenied");
        store("adminToken", null);
        lastEvents = null;
      } else if (!r.ok) {
        adminMsg.textContent = t("adminError");
      } else {
        lastEvents = (await r.json()).events;
        adminMsg.textContent = lastEvents.length ? "" : t("adminEmpty");
      }
    } catch (_) {
      adminMsg.textContent = t("adminError");
    }
    renderEvents();
  }

  function renderEvents() {
    const table = document.getElementById("events");
    if (!lastEvents || !lastEvents.length) {
      table.hidden = true;
      return;
    }
    table.hidden = false;
    table.tBodies[0].replaceChildren(
      ...lastEvents.map((e) => {
        let ack;
        if (e.acknowledged_at) ack = fmtTime(e.acknowledged_at);
        else if (e.severity === "warning") {
          ack = el("button", { type: "button" }, t("ackButton"));
          ack.addEventListener("click", () => acknowledge(e.id));
        } else ack = "–";
        return el(
          "tr",
          { class: e.severity },
          el("td", {}, fmtTime(e.occurred_at)),
          el("td", {}, e.slot_id),
          el("td", {}, t("kind_" + e.kind) + (e.simulated ? ` [${t("simulatedShort")}]` : "")),
          el("td", {}, e.detector ? t("det_" + e.detector) + (e.severity === "shadow" ? " " + t("shadow") : "") : "–"),
          el("td", {}, ack)
        );
      })
    );
  }

  async function acknowledge(id) {
    const token = store("adminToken");
    if (!token) return;
    try {
      await fetch(`/api/v1/events/${id}/ack`, { method: "POST", headers: { Authorization: "Bearer " + token } });
    } catch (_) {}
    loadEvents();
  }

  document.getElementById("admin-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const v = tokenInput.value.trim();
    tokenInput.value = "";
    if (v) store("adminToken", v); // nur für diese Browser-Sitzung
    loadEvents();
  });
  document.getElementById("admin-logout").addEventListener("click", () => {
    store("adminToken", null);
    lastEvents = null;
    adminMsg.textContent = "";
    renderEvents();
  });

  // Anzeige auch ohne neue Antwort regelmäßig neu bewerten (Verbindungsalter).
  setInterval(render, 1000);
  setInterval(() => store("adminToken") && loadEvents(), 10_000);

  applyLang();
  poll();
  pollSummary();
  loadEvents();
})();
