# Testprotokoll

Kein Test besteht allein deshalb, weil er einmal zufällig funktioniert hat. T04–T06 mehrfach
wiederholen. T08/T14 nur kontrolliert und nach Absprache mit der IT.

## Automatisierte Tests (ohne Hardware)

| Test | Automatisiert in | Stand 28.09. |
|---|---|---|
| T01 leerer Platz / T02 belegt | `backend/tests/test_api.py::test_T01_T02_free_and_occupied` | bestanden |
| T03 Empfehlung | `test_T03_recommendation_is_first_free_by_position` | bestanden |
| T05 Warnung | `test_T05_unusual_movement_creates_alert_after_grace` | bestanden |
| T06 normales Parken (Schonzeit) | `test_T06_parking_within_grace_period_no_alert`, `test_single_bump_no_alert` | bestanden |
| T07 Sensorausfall | `test_T07_sensor_error_is_unknown`, Gateway-Watchdog-Test | bestanden |
| T08 Netzunterbrechung | `test_stale_data_becomes_unknown`, Gateway-Puffertests, `test_old_buffered_data_does_not_trigger_alert` | bestanden |
| T09 API-Schutz | `test_T09_write_without_token_rejected_and_logged` | bestanden |
| T10 Leserecht | `test_T10_read_view_cannot_do_admin` | bestanden |
| T13 Verlauf | `test_T13_summary_time_weighted_and_labels_simulated` | bestanden |
| Doppelungen | `test_duplicate_sequence_is_ignored`, `test_batch_endpoint` | bestanden |

Zusätzlich Ende-zu-Ende mit Simulator (Simulator → Gateway → API → Browser, 28.09.):
Belegung, Empfehlung, Warnung beim Rütteln (Regel + KI-Schatten), Sensorfehler → „unbekannt“,
Simulator gestoppt → nach 30 s alle Plätze „unbekannt – Daten veraltet“, Quittieren im Browser,
Sprachen DE/NL/EN, schmale Ansicht (390 px). Alles mit **simulierten** Daten.

## Abnahmetests mit Hardware (Plan Kapitel 14)

| ID | Durchführung | Bestanden, wenn … | Datum | Version | Wdh. | Beobachtung | Ergebnis | Bearbeiter |
|---|---|---|---|---|---|---|---|---|
| T01 | Demo-Objekt entfernen | Platz nach Stabilisierung frei | | | | | offen | |
| T02 | Objekt einstellen | LED/Text und Dashboard belegt | | | | | offen | |
| T03 | mind. ein Platz frei | tatsächlich freier Platz empfohlen | | | | | offen | |
| T04 | mehrfach einstellen/entfernen | kein dauerhaftes Springen | | | | | offen | |
| T05 | kontrollierte Erschütterung | Warnung mit Platz/Zeit; Zeit bis Anzeige: ___ s | | | | | offen | |
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
