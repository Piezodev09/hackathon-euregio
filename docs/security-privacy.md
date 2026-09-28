# Sicherheit und Datenschutz

## Bedrohungsmodell und Umsetzung

| Risiko | Mögliches Problem | Gegenmaßnahme im Prototyp | Wo |
|---|---|---|---|
| Gefälschte Sensordaten | falsche Belegung/Meldungen | Geräte-Token, strikte Schema-Validierung (unbekannte Felder verboten, Wertebereiche), nur konfigurierte Plätze, Audit-Log | `schemas.py`, `main.py` |
| Zugriff auf Admin-Funktionen | Meldungen unberechtigt quittiert | getrennte Admin-Tokens, Quittieren protokolliert, Leseansicht ohne Schreibrechte | `require_admin` |
| Offene Netzwerkdienste | Angriff auf VM/Pi | nftables-Beispiel: nur 8443 aus Demo-Netz, SSH nur Admin-Netz; systemd-Härtung; keine OpenAPI-/Docs-Seiten | `deploy/` |
| Sensorausfall | falsches „frei“ | „unbekannt“ bei Fehler, Timeout, fehlenden Daten; Browser-Timeout | Arduino, Gateway, API, UI |
| Überlastung der API | keine aktuellen Anzeigen | Token-Bucket je Client für Lesen/Schreiben, Body ≤ 64 KB, Batch ≤ 200 | `ratelimit.py` |
| Geheimnis im Repository | Token-Missbrauch | Tokens nur aus Umgebung/Datei (Rechte 600/640), `.gitignore`, Beispiele nur mit Platzhaltern, Tokens < 16 Zeichen werden abgelehnt | `config.py` |
| Zu lange Speicherung | unnötige Nachvollziehbarkeit | Rohdaten automatisch nach 72 h gelöscht, Löschskript nach Demo | `retention`, `delete-demo-data.sh` |
| Alarm-Fehlinterpretation | unberechtigter Verdacht gegen Menschen | sachlicher Text „ungewöhnliche Erschütterung … bitte prüfen“, keine Personenzuordnung, Hinweis im Dashboard | `i18n.js` |
| XSS im Dashboard | Code-Einschleusung | keine `innerHTML`-Nutzung, strikte CSP (`script-src 'self'`), `X-Frame-Options: DENY` | `app.js`, `main.py` |
| Timing-Angriff auf Token | Token erraten | Vergleich mit `hmac.compare_digest` | `main.py` |

## OWASP-orientierte Mindestprüfung

- [x] Eingabevalidierung für jeden Endpunkt
- [x] Berechtigungsprüfung für jeden schreibenden Endpunkt
- [x] Keine Debug-Ausgaben/Stacktraces nach außen, Validierungsfehler spiegeln keine Eingaben
- [x] Keine Standardpasswörter (Mindestlänge, Installationsskript erzeugt Zufallstokens)
- [x] Admin-Zugriffe und abgewiesene Schreibversuche protokolliert (`audit_log`)
- [x] Test unberechtigter Schreibversuch (T09) automatisiert
- [ ] TLS mit vertrauenswürdigem Zertifikat – mit IT klären (Demo: selbst signiert + `ca_file` im Gateway)
- [ ] Firewallregeln auf echte Netze angepasst und getestet
- [ ] Updates von VM und Pi eingespielt

## Token-Handhabung

- Erzeugen: `python3 -c "import secrets;print(secrets.token_urlsafe(32))"`
- VM: `/etc/bike-station/api.env` (`BIKE_DEVICE_TOKENS`, `BIKE_ADMIN_TOKENS`, kommagetrennt → Tokenwechsel ohne Ausfall möglich)
- Pi: `/etc/bike-gateway/gateway.env` (`BIKE_DEVICE_TOKEN`)
- Browser: Admin-Token nur in `sessionStorage` der aktuellen Sitzung, nie in Folien/Screenshots.
- Wechsel: neues Token ergänzen, Pi umstellen, altes entfernen, Dienste neu starten.

## Datenschutzentscheidungen

- Für die Kernfunktion **keine** Kameras, RFID-Tags, Namen oder Standortprofile.
- Gespeichert: Station/Platz, technischer Zustand, Vibrationswert, Zeitpunkt, Ereignistyp, Quelle (live/simuliert).
  IP-Adressen nur im Audit-Log bei Admin-Aktionen und abgewiesenen Zugriffen.
- Auch diese Daten können im Schulkontext sensibel sein, wenn sie mit konkreten Situationen verknüpft werden.
- **Nicht zulässig:** „Person X hat ein Fahrrad gestohlen.“
  **Zulässig:** „An Platz A wurde eine ungewöhnliche Erschütterung gemessen; bitte Situation prüfen.“

Vor realem Betrieb klären: verantwortliche Stelle, Zweck, Zugang, Speicherdauer, Information
der Betroffenen, Freigabe durch IT/Datenschutzverantwortliche. Die 72-h-Frist ist ein Demo-Vorschlag,
keine rechtliche Freigabe.
