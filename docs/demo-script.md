# Regieplan Abschlussdemo

## Vorher (Checkliste)

- [ ] Präsenz- und Erschütterungssensor am Stellplatz getestet, Display oben vorne lesbar
- [ ] Pi: `systemctl status bike-gateway` läuft, VM: `/health` ok, `ai_model_available` wie erwartet
- [ ] Dashboard auf Anzeige geöffnet, Sprache gewählt, Browser-Zoom passend
- [ ] Stromversorgung/Akkus gesichert, Kabel gegen Stolpern gesichert
- [ ] Backup/Snapshot erstellt, Screenshots + Testprotokoll als Fallback bereit
- [ ] Rollen: Technik vorführen · Architektur/Grenzen erklären · Störungen beobachten
- [ ] Admin-Token nicht auf Folien/Bildschirm sichtbar

## Ablauf

1. **Problem in einem Satz:** „Ein Stellplatz zeigt ehrlich, ob er frei ist – und sagt ‚unbekannt‘, wenn er es nicht sicher weiß.“
2. Leeren Stellplatz zeigen: Display und Dashboard zeigen FREI.
3. Fahrrad/Demo-Objekt einstellen: Display und Dashboard wechseln auf BELEGT (≤ 5 s).
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
Dashboard sichtbar „SIMULATION“. Nur das Design zeigen: `design/smart-bicycle-box-prototyp.html` (als Demodaten gekennzeichnet).
