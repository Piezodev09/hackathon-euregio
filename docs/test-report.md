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
| TLS: Befehle mit angeheftetem Zertifikat, `/install/server.crt` | `test_agent.py::test_pinned_install_commands_for_self_signed_cert`, `test_no_cert_endpoint_without_tls` | bestanden |
| NFC: Karte anlernen, Check-in/-out mit Gebühr | `test_parking.py::test_unknown_card_is_learned_then_check_in_out_with_fee` | bestanden |
| NFC: fremde Karte, Doppelungen, verspätete Taps | `test_other_card_duplicates_blocked_and_expired` | bestanden |
| NFC: Mandantentrennung, Wartungsmodus, Rollen | `test_nfc_tenant_isolation`, `test_maintenance_blocks_taps_and_is_in_status`, `test_manual_close_cancel_and_roles` | bestanden |
| Gebührenberechnung (Tag/Stunde/pauschal, Freiminuten, Höchstbetrag, Mitternacht) | `test_compute_fee` (parametrisiert), `test_month_helpers` | bestanden |
| Lizenz: Stellplatz-Tage, Testphase kostenlos, Vertragspreis, Rechnung, CSV | `test_license_invoice_per_stall_day`, `test_license_invoice_issue_and_custom_price` | bestanden |
| Kamera: aus per Voreinstellung, Freigabe nötig, Tarif-Merkmal | `test_camera.py::test_camera_off_by_default_and_needs_approval`, `test_free_plan_has_no_camera` | bestanden |
| Kamera: Upload-Grenzen, nur Admin, Audit, Aufbewahrung, Löschen | `test_upload_view_audit_retention_and_disable`, `test_deleting_station_removes_images_from_disk` | bestanden |
| Kamera: Bild bei Warnung nur wenn aktiviert | `test_alert_requests_capture_only_when_enabled` | bestanden |
| Stellplatz-Ansicht: Link, Rotation, „Problem melden“ mit Ratenbegrenzung | `test_stall_view.py` (3 Tests) | bestanden |
| Gateway: NFC-Zeilen, Versand, Rückmeldung an Arduino | `pi-gateway/tests/test_gateway.py::test_parse_nfc_line`, `test_gateway_sends_nfc_tap_immediately_and_reports_back`, `test_gateway_nfc_rejected_is_not_retried` | bestanden |
| Agent: Kamera-Erkennung, Upload bei Warnung, `doctor` | `pi-gateway/tests/test_agent.py` (Kamera-, Upload- und Doctor-Tests) | bestanden |

Alles zusammen: `scripts/check.sh` → Backend 86, Gateway 43 Tests bestanden; JS-Syntax, Übersetzungen
DE/NL/EN vollständig, Shell-Syntax ok.

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

### TLS mit selbst signiertem Zertifikat (Ende-zu-Ende, 28.09., ohne echten Pi)

`deploy/make-cert.sh` mit IP-Adresse → Plattform mit TLS gestartet → Originalfehler reproduziert
(`curl -fsSLO https://…/install/agent.sh` → `curl: (60) … self-signed certificate`) → falscher Pin wird
abgelehnt (`curl: (90) public key does not match pinned public key`) → exakt die Portal-Befehle 1–3
(Zertifikat per Pin laden, Skript mit `--cacert`, `sha256sum -c` → `OK`) → Schritt 4 als root mit
`--no-systemd --source simulator` → gekoppelt, `ca_file` gespeichert → Heartbeat und Messungen über TLS angekommen.

### NFC, Parkgebühren, Kamera, Stellplatz-Ansicht (Ende-zu-Ende, 28.09., Simulator)

`scripts/dev.sh` (Agent mit Simulator, der auch die Demo-Karte tappt) + Playwright:
Kamera im Portal mit Freigabe eingeschaltet (200) → *Testbild aufnehmen* (200) → Agent lädt das Simulator-Testbild
hoch → Bild im Portal sichtbar, als simuliert gekennzeichnet. Parkvorgänge entstehen per NFC-Tap, Beträge nach Tarif,
Monatsaufstellung und CSV. Stellplatz-Ansicht auf 390 px ohne Querscrollen, „Problem melden“ erzeugt ein Ereignis.
Kiosk-Anzeige, Lizenz- und Rechnungsseiten der Plattform geprüft, keine Konsolen-/CSP-Fehler.

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

- Arduino-Sketch noch nicht auf echter Hardware kompiliert/getestet – auch nicht mit `NFC_ENABLED` (PN532).
- NFC nur mit Simulator getestet; echter PN532-Leser und echte Karten noch nicht angeschlossen.
- Kamera nur mit dem Testbild des Simulators getestet; `rpicam-still`/`fswebcam`/`ffmpeg`-Aufrufe nur als
  Befehlszeilen geprüft, nicht mit echter Kamera.
- KI-Modell bisher nur mit simulierten Daten trainiert.
- NL/EN-Texte noch nicht von sprachkundiger Person geprüft.
