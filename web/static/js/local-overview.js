// Übersicht aller Stellplätze an diesem Raspberry Pi (lokale Anzeige, http://127.0.0.1:8088/local).
// Zustand immer mit Wort + Symbol + Farbe; ohne gültige Messung STATUS UNBEKANNT, nie „frei“.
(() => {
  const I = window.I18N;
  const COUNT = {
    de: (f, n) => `${f} von ${n} Stellplätzen frei`, nl: (f, n) => `${f} van ${n} fietsplekken vrij`, en: (f, n) => `${f} of ${n} stalls free`,
  };
  const SYMBOL = { free: "✓", occupied: "■", reserved: "◆", unknown: "?" };
  let lang = "de";
  try { lang = localStorage.getItem("lang") || (navigator.language || "de").slice(0, 2); } catch (_) { /* optional */ }
  if (!I[lang]) lang = "de";
  let data = null;

  function render() {
    const T = I[lang];
    document.documentElement.lang = lang;
    const list = document.getElementById("stalls");
    list.replaceChildren();
    if (!data) return;
    const stalls = data.stalls;
    const free = stalls.filter((s) => s.state === "free" && !s.maintenance && !s.closed).length;
    document.getElementById("count").textContent = COUNT[lang](free, stalls.length);
    const off = document.getElementById("offline");
    off.hidden = !stalls.some((s) => s.offline);
    off.textContent = T.offline;
    for (const s of stalls) {
      const state = s.maintenance ? "unknown" : s.state;
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = "/local?stall=" + encodeURIComponent(s.station_id);
      a.className = "sbb-status ov-card";
      a.dataset.state = state;
      const sym = document.createElement("span");
      sym.className = "ov-sym";
      sym.setAttribute("aria-hidden", "true");
      sym.textContent = SYMBOL[state] || "?";
      const name = document.createElement("span");
      name.className = "ov-name";
      name.textContent = s.display_name || s.station_id;
      const word = document.createElement("span");
      word.className = "sbb-status__word";
      word.textContent = s.maintenance ? T.maintenance.split(" – ")[0] : (T[state] || T.unknown);
      a.append(sym, name, word);
      li.append(a);
      list.append(li);
    }
  }

  async function poll() {
    try {
      const r = await fetch("/local/overview", { cache: "no-store" });
      data = r.ok ? await r.json() : null;
    } catch (_) {
      data = null; // Agent nicht erreichbar: nichts anzeigen statt veralteter Zustände
    }
    render();
  }

  document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => {
    lang = b.dataset.lang;
    try { localStorage.setItem("lang", lang); } catch (_) { /* optional */ }
    render();
  }));
  poll();
  setInterval(poll, 2000);
})();
