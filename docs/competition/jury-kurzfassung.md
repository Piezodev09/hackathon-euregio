# Smart Bike Station – Kurzfassung für die Jury

**Jeder Stellplatz live – kein Suchen mehr.** Kleine Sensoren erkennen je Stellplatz, ob ein Fahrrad
steht. Ein Bildschirm an der Station und jedes Handy (per QR-Code) zeigen freie Plätze an und
empfehlen einen. Ungewöhnliche Bewegung an einem belegten Platz wird als **Verdacht gemeldet – nie
als Diebstahlbeweis**. Keine Kameras, keine Namen.

## Wie es funktioniert

| Baustein | Aufgabe |
|---|---|
| Arduino | liest Anwesenheits- und Vibrationssensor je Platz, entprellt 2 s, steuert LEDs |
| Raspberry Pi | prüft die Messwerte, puffert bei Netzausfall, überträgt verschlüsselt, meldet seinen Zustand |
| Proxmox | die ganze Plattform in einem Container: API, Datenbank, KI-Auswertung, Portal, Kiosk |
| Browser | Kiosk-Anzeige, Kundenportal, Landingpage – Deutsch, Niederländisch, Englisch |

## Was die Lösung auszeichnet

1. **„Unbekannt“ ist nie „frei“.** Fehlen Daten, sind sie veraltet oder meldet ein Sensor einen
   Fehler, zeigt die Station „unbekannt“ – abgesichert an vier Stellen (Arduino, Pi, Plattform,
   Browser). Fällt ein Gateway aus, kommt genau eine Meldung.
2. **Ehrliche KI.** Regel und KI (Isolation Forest) werden auf denselben Testläufen verglichen. Auf
   unseren – simulierten – Daten ist die KI *nicht* besser (Regel 9/9 erkannt, 4/45 Fehlalarme;
   KI 9/9, 5/45). Darum bleibt die Regel sichtbar, die KI läuft im Schattenbetrieb mit.
3. **Selbst betreibbar.** Ein Befehl auf dem Proxmox-Host installiert alles – ohne Domain, ohne
   Mailserver, mit eigener Zertifizierungsstelle und täglichen Backups. Alternativ Docker.
4. **Gateway in 60 Sekunden.** Kopplungscode im Portal, ein Befehl auf dem Pi – oder das
   Home-Assistant-Add-on, dann steckt der Arduino direkt am Home-Assistant-Rechner.
5. **Sicherheit wie bei einer professionellen Plattform.** 2FA, Mandantentrennung, Rollen,
   Audit-Log, signierte Webhooks, Lese-API-Schlüssel, strenge Sicherheitsheader.
6. **Barrierefrei.** Symbole und Text statt nur Farbe, volle Tastaturbedienung, Kontraste nach
   WCAG 2.2 AA – automatisch geprüft (axe-core, 0 schwerwiegende Befunde).
7. **Anschlussfähig.** Home Assistant (MQTT), Teams/Slack/Discord, REST-API.

## Belege

- 178 automatisierte Tests (116 Plattform, 62 Agent), alle grün.
- Ende-zu-Ende geprüft: Installation im Container, Kopplung über HTTPS mit gepinnter CA,
  Webhooks an einen Testempfänger, MQTT gegen einen echten Broker, Docker-Images,
  Home-Assistant-Add-on, Firmware im AVR-Simulator mit dem Parser des Agents.
- Barrierefreiheit automatisch geprüft: axe-core auf 26 Seitenzuständen, 0 schwerwiegende Befunde.
- Jede Anforderung mit Datei, Test, Screenshot und Demo-Schritt: [criteria-matrix.md](criteria-matrix.md).

## Ehrliche Grenzen

- Der Arduino-Sketch kompiliert ohne Warnungen und läuft im Simulator (simavr), aber noch nicht
  auf der endgültigen Hardware; Pins und Spannungen werden vor Ort geprüft.
- Die KI ist bisher nur mit simulierten Daten trainiert; echte Aufnahmen folgen.
- Latenz (Ziel ≤ 5 s) und Energiebedarf (ca. 4 W je Station, *Schätzung*) werden vor Ort gemessen.
- Nicht auf einem echten Proxmox-Host bzw. in Home Assistant selbst getestet (nur Trockenlauf bzw.
  Container mit nachgebildeter Supervisor-API).

## Geschäftsmodell

Kostenlos für eine Station, 19 €/Monat für Schulen, 79 €/Monat Pro – oder selbst gehostet für
Kommunen. Hardware-Starterkit ca. 130 € je Station mit 3 Plätzen (*Schätzung*).

**Demo:** 7 Minuten, 7 Momente – [demo-script.md](demo-script.md) (Englisch).
