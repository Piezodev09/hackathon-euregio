# Regieplan Abschlussdemo

## Vorher (Checkliste)

- [ ] Sensoren an allen Plätzen getestet, LEDs sichtbar, Beschriftung angebracht
- [ ] Pi: `systemctl status bike-gateway` läuft, VM: `/health` ok, `ai_model_available` wie erwartet
- [ ] Dashboard auf Anzeige geöffnet, Sprache gewählt, Browser-Zoom passend
- [ ] Stromversorgung/Akkus gesichert, Kabel gegen Stolpern gesichert
- [ ] Backup/Snapshot erstellt, Screenshots + Testprotokoll als Fallback bereit
- [ ] Rollen: Technik vorführen · Architektur/Grenzen erklären · Störungen beobachten
- [ ] Admin-Token nicht auf Folien/Bildschirm sichtbar

## Ablauf

1. **Problem in einem Satz:** „Wir zeigen freie Fahrradstellplätze ohne Suche und erkennen auffällige Bewegungen.“
2. Leeren Platz und Empfehlung im Dashboard zeigen.
3. Fahrrad/Demo-Objekt einstellen: LED/Text am Platz und Webanzeige wechseln (≤ 5 s).
4. Kontrolliert bewegen (nach Schonzeit ~15 s): Warnhinweis erscheint –
   **erläutern, dass dies kein Diebstahlnachweis ist.** Im Verwaltungsbereich quittieren.
5. Sensorkabel nur wenn sicher vorbereitet trennen: „unbekannt“ statt falschem „frei“.
6. Systemzeichnung: Arduino → Pi → Proxmox-VM → Browser (`docs/architecture.md`).
7. KI: Regel und Modell vergleichen (`ml/report.md`, Schatten-Ereignisse in der Verwaltung);
   echte und simulierte Daten klar benennen.
8. Datenschutz, Barrierefreiheit (DE/NL/EN umschalten, Tastatur, Symbole statt nur Farbe),
   Energie und mögliche Weiterentwicklung zusammenfassen.

## Fallback bei Störung

Aufgezeichnetes Testprotokoll und Screenshots zeigen und offen sagen, welcher Teil gerade nicht
live funktioniert. **Kein verdeckter Wechsel auf den Simulator** – wird er genutzt, zeigt das
Dashboard sichtbar „simulierte Daten“.
