# Testprotokoll

Kein Test besteht allein deshalb, weil er einmal zufällig funktioniert hat. T04–T06 mehrfach
wiederholen. T08/T14 nur kontrolliert und nach Absprache mit der IT.

## Automatisierte Tests (ohne Hardware)

| Test | Automatisiert in | Stand |
|---|---|---|
| T01 leerer Stellplatz / T02 belegt | `backend/tests/test_api.py::test_T01_T02_free_and_occupied` | bestanden |
| Keine Daten = unbekannt, nie frei | `test_no_data_is_unknown_never_free`, `test_future_timestamp_is_unknown` | bestanden |
| Genau ein Stellplatz je Station | `test_station_is_exactly_one_stall`, `test_migration_to_single_stall` | bestanden |
| T05/T06 Warnung, Schonzeit | `test_T05_T06_alert_after_grace_but_not_while_parking`, `test_single_bump_no_alert_and_old_buffered_data_ignored` | bestanden |
| T07 Sensorausfall / veraltet | `test_T07_sensor_error_and_stale_are_unknown`, Gateway-Watchdog-Test | bestanden |
| T09 API-Schutz | `test_T09_device_write_without_or_with_bad_token_rejected_and_logged` | bestanden |
| T10 Rollen | `test_roles`, `test_cooldown_ack_and_roles` | bestanden |
| T13 Verlauf | `test_T13_summary_time_weighted_and_labels_simulated` | bestanden |
| Doppelungen | `test_duplicate_sequence_is_ignored`, `test_batch_endpoint` | bestanden |

Zusätzlich Ende-zu-Ende mit Simulator (Agent mit Simulator → API → Browser): Anzeige FREI/BELEGT,
„SIMULATION“-Kennzeichnung in Portal und Kiosk-Anzeige, Sprachen DE/NL/EN, schmale Ansicht (390 px)
ohne Querscrollen, Hell/Dunkel. Alles mit **simulierten** Daten.

### Agent-Installation (Ende-zu-Ende, 28.09., ohne echten Pi)

Kopplungscode im Portal erzeugt → exakt die angezeigten Befehle ausgeführt (Download, `sha256sum -c`
→ OK, `agent.sh --code … --no-systemd --source simulator`) → Gerät gekoppelt, Zustandsdatei `0600`,
gleicher Code zweites Mal abgelehnt → Agent gestartet, im Portal *online* mit Zustandsdaten →
*Token erneuern* →
Rotation ohne Unterbrechung → *Neu starten* → Agent beendet sich mit Code 3 (systemd startet neu) →
Plattform mit Agent-Version 1.0.1 → Agent lädt Update, prüft SHA-256, schaltet um, startet neu,
meldet 1.0.1 und bestätigt das Update. **Offen:** Test auf echtem Raspberry Pi mit systemd und Arduino.

## Abnahmetests mit Hardware (Plan Kapitel 14)

| ID | Durchführung | Bestanden, wenn … | Datum | Version | Wdh. | Beobachtung | Ergebnis | Bearbeiter |
|---|---|---|---|---|---|---|---|---|
| T01 | Demo-Objekt entfernen | Stellplatz nach Stabilisierung FREI | | | | | offen | |
| T02 | Objekt einstellen | Display und Dashboard BELEGT | | | | | offen | |
| T03 | Arduino/Pi trennen | Display und Dashboard STATUS UNBEKANNT (nie FREI) | | | | | offen | |
| T04 | mehrfach einstellen/entfernen | kein dauerhaftes Springen | | | | | offen | |
| T05 | kontrollierte Erschütterung | Warnung mit Zeit; Zeit bis Anzeige: ___ s | | | | | offen | |
| T06 | regulär einstellen/entfernen | Fehlalarme gezählt: ___ von ___ | | | | | offen | |
| T07 | Sensorverbindung trennen | unbekannt/Fehler, nie falsches frei | | | | | offen | |
| T08 | Pi–VM trennen | Fehler sichtbar; danach Synchronisation | | | | | offen | |
| T09 | ohne Token schreiben | abgewiesen und protokolliert | | | | | offen | |
| T10 | Ansicht ohne Admin | keine Admin-Aktion möglich | | | | | offen | |
| T11 | DE/NL/EN | Kernzustände und Warnung übersetzt | | | | | offen | |
| T12 | Tastatur / ohne Farbe (Graustufen) | verständlich bedienbar | | | | | offen | |
| T13 | mehrere Messungen | Diagramm korrekt, Datenquelle gekennzeichnet | | | | | offen | |
| T14 | Pi/VM neu starten | Dienste laufen an, Status neu erfasst | | | | | offen | |
| T15 | gleiche Fälle Regel vs. KI | Ergebnisse und Grenzen gezeigt (`ml/report.md`) | | | | | offen | |

Latenz messen (Plan 5.3): Zeitpunkt des stabilen Sensorwechsels und des sichtbaren
Dashboard-Wechsels notieren. Ziel ≤ 5 s (2 s Entprellung + Übertragung + 2 s Polling).

## Bekannte Einschränkungen / Fehler

- Arduino-Sketch noch nicht auf echter Hardware kompiliert/getestet.
- KI-Modell bisher nur mit simulierten Daten trainiert.
- NL/EN-Texte noch nicht von sprachkundiger Person geprüft.
