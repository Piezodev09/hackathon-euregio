# Regieplan Abschlussdemo

## Vorher (Checkliste)

- [ ] Präsenz- und Erschütterungssensor am Stellplatz getestet, Display oben vorne lesbar
- [ ] Pi: `systemctl status bike-agent` läuft, VM: `/health` ok, `ai_model_available` wie erwartet
- [ ] Dashboard auf Anzeige geöffnet, Sprache gewählt, Browser-Zoom passend
- [ ] Stromversorgung/Akkus gesichert, Kabel gegen Stolpern gesichert
- [ ] Backup/Snapshot erstellt, Screenshots + Testprotokoll als Fallback bereit
- [ ] Rollen: Technik vorführen · Architektur/Grenzen erklären · Störungen beobachten
- [ ] Admin-Token nicht auf Folien/Bildschirm sichtbar
- [ ] Pi: `sudo bike-agent doctor` ohne Fehler (Arduino-Port, Plattform/Zertifikat, Uhrzeit)
- [ ] Demo-Karte im Portal unter **Karten** freigegeben, Tarif unter **Parkgebühren** gesetzt
- [ ] QR-Aufkleber der Stellplatz-Ansicht ausgedruckt (Stellplatz → Einstellungen → Stellplatz-Ansicht → Aufkleber drucken)
- [ ] Kamera: nur wenn die Freigabe wirklich vorliegt – sonst aus lassen und nur erklären
- [ ] Ohne Hardware: `scripts/demo-reset.sh --yes` (frische Demo mit Karte, Tarif, 7 Tagen simulierter Historie)

## Ablauf

1. **Problem in einem Satz:** „Ein Stellplatz zeigt ehrlich, ob er frei ist – und sagt ‚unbekannt‘, wenn er es nicht sicher weiß.“
2. Leeren Stellplatz zeigen: Display und Dashboard zeigen FREI.
3. Fahrrad/Demo-Objekt einstellen: Display und Dashboard wechseln auf BELEGT (≤ 5 s).
4. Kontrolliert bewegen (nach Schonzeit ~15 s): Warnhinweis erscheint –
   **erläutern, dass dies kein Diebstahlnachweis ist.** Im Verwaltungsbereich quittieren.
5. Sensorkabel nur wenn sicher vorbereitet trennen: „unbekannt“ statt falschem „frei“.
6. **Ein-/Auschecken:** Karte an den NFC-Leser halten → Display „Eingecheckt“, Portal zeigt den Parkvorgang.
   Erneut halten → „Ausgecheckt · 0,50 €“. Eine unbekannte Karte zeigen: erscheint unter **Karten** als
   „wartet auf Freigabe“. (Ohne Leser: Simulator `n` – dann ist alles als SIMULATION gekennzeichnet.)
7. **Stellplatz-Ansicht:** QR-Code mit dem Handy scannen → Status, Preis, laufender Betrag,
   „Problem melden“ → Meldung erscheint im Portal unter Ereignisse.
8. **Abrechnung:** Portal → Parkgebühren (Monatsaufstellung je Karte, CSV) und Tarif & Nutzung
   (Lizenz: Preis je Stellplatz und Tag, Testphase kostenlos). Plattform-Admin: Rechnungen je Kunde festschreiben.
   Sagen: **keine Zahlungsanbindung**, Bezahlung per Rechnung.
9. **Wartungsmodus** kurz einschalten → Display „AUSSER BETRIEB“, Karten werden abgelehnt.
10. Nur mit Freigabe: **Kamera** – Testbild aufnehmen, auf Löschfrist und Audit-Log hinweisen.
11. Systemzeichnung: Arduino → Pi → Proxmox-VM → Browser (`docs/architecture.md`).
12. KI: Regel und Modell vergleichen (`ml/report.md`, Schatten-Ereignisse in der Verwaltung);
   echte und simulierte Daten klar benennen.
13. Datenschutz, Barrierefreiheit (DE/NL/EN umschalten, Tastatur, Symbole statt nur Farbe),
   Energie und mögliche Weiterentwicklung zusammenfassen.

## Fallback bei Störung

Aufgezeichnetes Testprotokoll und Screenshots zeigen und offen sagen, welcher Teil gerade nicht
live funktioniert. **Kein verdeckter Wechsel auf den Simulator** – wird er genutzt, zeigt das
Dashboard sichtbar „SIMULATION“. Nur das Design zeigen: `design/smart-bicycle-box-prototyp.html` (als Demodaten gekennzeichnet).
