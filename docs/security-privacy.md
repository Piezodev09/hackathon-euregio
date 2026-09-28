# Sicherheit und Datenschutz

Die Plattform ist mandantenfähig (SaaS): Kunden (Organisationen) mieten sich ein und verwalten
ihre Stationen, Geräte und Teams selbst. Grundlage der Maßnahmen sind OWASP ASVS (Level 2
als Ziel), OWASP Top 10 und NIST SP 800-63B für Passwörter.

## Umgesetzte Sicherheitsmaßnahmen

### Identität und Anmeldung
| Maßnahme | Umsetzung | Code |
|---|---|---|
| Passwort-Hashing | scrypt (N=2¹⁵, r=8, p=1, 16-Byte-Salt), automatisches Rehash bei höherem Kostenfaktor | `security.py` |
| Passwortrichtlinie | ≥ 12 Zeichen, ≤ 128, Liste häufiger Passwörter, nicht Teil von E-Mail/Name, keine Zwangs-Sonderzeichen (NIST) | `password_problems` |
| Keine Konto-Aufzählung | gleiche Antwort bei unbekannter E-Mail, falschem Passwort und gesperrtem Konto; Dummy-Hash gleicht Laufzeit an; Registrierung/Reset immer „E-Mail prüfen“ | `routes/auth.py` |
| Brute-Force-Schutz | Kontosperre nach 5 Fehlversuchen mit wachsender Dauer (5, 10, 20 … min, max. 24 h), Hinweis-Mail; IP-Ratenbegrenzung 10/min für Auth, 5/h für Mail-auslösende Endpunkte | `_register_failure`, `Core` |
| E-Mail-Bestätigung | Pflicht vor erster Anmeldung; Einmal-Token, 48 h gültig | `verify-email` |
| Zwei-Faktor (TOTP) | RFC 6238, ±1 Zeitschritt, **Replay-Schutz** (letzter Schritt gespeichert), 10 Wiederherstellungscodes (nur gehasht), Pflicht per Organisation einstellbar, Pflicht für Plattform-Admins | `totp_verify`, `mfa/*` |
| 2FA-Geheimnis im Ruhezustand | AES-256-GCM mit Associated Data (Nutzer-ID) – Datenbankdiebstahl allein reicht nicht | `SecretBox` |
| Passwort-Reset | Einmal-Token, 1 h gültig, beendet alle Sitzungen, Benachrichtigung | `password/reset` |
| Sicherheitsbenachrichtigungen | Mails bei Sperre, Passwortänderung, 2FA an/aus, Nutzung eines Wiederherstellungscodes | |

### Sitzungen
| Maßnahme | Umsetzung |
|---|---|
| Serverseitige Sessions | 256-Bit-Zufallstoken, in der DB nur SHA-256-Hash, sofort widerrufbar |
| Cookie | `__Host-`-Präfix (bei HTTPS), `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/` |
| Laufzeit | 60 min Leerlauf, 12 h absolut |
| Session-Fixation | bei jeder Anmeldung neue Session, alte wird verworfen |
| Widerruf | Liste aktiver Sitzungen, einzeln/alle anderen abmelden; Passwort-/Rollenänderung und 2FA-Aktivierung beenden andere Sitzungen; Sperre eines Mandanten beendet alle |
| CSRF | Synchronizer-Token (`X-CSRF-Token`) für jede zustandsändernde Anfrage **plus** Origin-/`Sec-Fetch-Site`-Prüfung **plus** SameSite=Strict |

### Autorisierung und Mandantentrennung
- Jede Abfrage ist über `tenant_id` gefiltert; fremde Ressourcen liefern **404** (keine Existenz-Auskunft).
- Nicht erratbare IDs (`st_…`, `usr_…`, 96 Bit) statt fortlaufender Nummern.
- Rollen: Inhaber > Administrator > Betreuer > Lesend. Niemand vergibt höhere Rechte als die eigenen;
  der letzte Inhaber kann weder entfernt noch herabgestuft werden.
- Geräte-Tokens gelten nur für **eine** Station (`station_mismatch` bei Abweichung), sind widerrufbar,
  nur gehasht gespeichert und werden genau einmal angezeigt.
- Agent-Kopplung per Einmal-Code (~50 Bit, 30 min, nur gehasht, rate-limitiert); Geräte-Tokens rotieren
  automatisch alle 30 Tage mit kurzer Übergangsfrist; Fernbefehle nur aus fester Liste; Updates nur mit
  passender SHA-256, sicherem Entpacken und automatischem Rollback (Details: [agent.md](agent.md)).
- Öffentliche Anzeige-Links: nur lesend, eingeschränkte Daten (keine KI-/Ereignisdetails), rotierbar,
  deaktivierbar; Token im URL-Fragment und im Header – nie in Server-Logs.
- Tarif-Limits (Stellplätze, Nutzer, Funktionen) werden serverseitig erzwungen.
- Plattform-Admins sind von Mandanten getrennt und benötigen 2FA.

### Transport, Header, Eingaben
| Maßnahme | Umsetzung |
|---|---|
| TLS | HTTPS Pflicht in `production` (Start verweigert sonst); HSTS 2 Jahre inkl. Subdomains |
| Content-Security-Policy | `default-src 'none'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'` – kein Inline-JS/CSS |
| Weitere Header | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy` (Kamera, Mikrofon, Standort … aus), COOP/CORP `same-origin`, `Cache-Control: no-store` für API und Seiten, `X-Request-ID` |
| Host-Header | `TrustedHostMiddleware` mit Allowlist |
| Eingaben | strikte Pydantic-Schemata (`extra=forbid`), Längen, Muster, Steuerzeichen verboten; E-Mail-Header-Injection damit ausgeschlossen |
| Anfragegröße | 64 KB, auch bei chunked Transfer |
| Fehlerausgaben | keine Stacktraces, keine gespiegelten Eingaben, keine OpenAPI/Docs-Seiten |
| XSS im Frontend | kein `innerHTML`; alle Inhalte als Textknoten |
| SQL-Injection | ausschließlich parametrisierte Abfragen |
| `security.txt` | `/.well-known/security.txt` für Meldungen von Schwachstellen |

### Betrieb
- Unsichere Konfigurationen werden in `production` abgelehnt (HTTP, `*`-Hosts, Console-Mail, schwacher scrypt-Faktor, fehlender Datenschlüssel).
- Datenbankdatei `0600`, `secure_delete`, systemd-Härtung (`NoNewPrivileges`, `ProtectSystem=strict`, …), nftables-Beispiel.
- Audit-Log je Mandant (Anmeldungen, Fehlversuche, Rollen, Tokens, Tarif, Export, Quittierungen …) und plattformweit.
- Automatische Löschung: Messdaten/Ereignisse nach Tarif-Frist (7/30/90 Tage), Audit-Log nach 365 Tagen, abgelaufene Sessions/Tokens.

## Bedrohungsmodell (Auszug)

| Bedrohung | Gegenmaßnahme |
|---|---|
| Credential Stuffing / Brute Force | Kontosperre, IP-Limit, 2FA, keine Aufzählung |
| Session-Diebstahl | HttpOnly-Cookie, kurze Laufzeiten, Widerruf, CSP gegen XSS |
| CSRF | Token + Origin + SameSite=Strict |
| Mandant A liest Daten von B (IDOR) | Tenant-Filter in jeder Abfrage, 404, nicht erratbare IDs, Tests `test_tenancy.py` |
| Gefälschte Sensordaten | Geräte-Token je Station, Validierung, Audit |
| Gestohlenes Geräte-Token | nur eine Station betroffen, widerrufbar, automatische Rotation, „zuletzt gesehen“ + IP sichtbar |
| Manipuliertes Agent-Update | SHA-256 über authentifizierten Kanal, sicheres Entpacken, Versionsprüfung, Rollback (offen: Release-Signatur) |
| Erratener Kopplungscode | 50 Bit, 30 min, einmalig, Rate-Limit je IP |
| DB-Leak | Passwörter scrypt, Tokens gehasht, 2FA-Geheimnisse AES-GCM |
| Missbrauch von Anzeige-Links | nur Lesen, begrenzte Daten, rotierbar, Rate-Limit |
| Fehlinterpretation eines Alarms | sachlicher Text, kein Personenbezug |
| Gefälschtes Zertifikat beim Pi-Setup (MITM) | Zertifikat wird nur mit passendem SPKI-Pin geladen (`curl --pinnedpubkey`), danach nur noch mit `--cacert` |
| Kartennummern-Leak | nur HMAC-SHA256 mit Datenschlüssel gespeichert, je Mandant verschieden; die UID wird nirgends angezeigt (neue Karten erkennt man am Zeitpunkt und Stellplatz des Taps) |
| Wiederholte/verspätete NFC-Taps | Sequenz je Gerät (Doppelungen verworfen), Taps älter als 60 s verworfen |
| Missbrauch der Stellplatz-Ansicht / „Problem melden“ | eigenes rotierbares Token, nur Lesen + Meldung, 3 Meldungen je 10 min je IP, max. 300 Zeichen, keine Personendaten |
| Kamerabilder in falschen Händen | aus per Voreinstellung, Freigabe-Vermerk Pflicht, nur Admins, jeder Abruf im Audit-Log, Dateien `0600`, Löschung nach 1–72 h, `Cache-Control: no-store` |
| Bösartiger Bild-Upload | nur Geräte-Token der Station, nur `image/jpeg` mit JPEG-Signatur, max. 2 MB (Limit je Pfad), zufälliger Dateiname |

## Datenschutz

- Messdaten: Stellplatz, Zustand, Erschütterungswert, Zeit, Quelle. Keine Personenerkennung.
- **NFC:** gespeichert werden nur der HMAC der Karten-UID, eine frei gewählte Bezeichnung (kein Name nötig),
  Parkvorgänge (Stellplatz, Beginn, Ende, Betrag). Wer welche Karte besitzt, weiß nur die Organisation.
- **Kamera (optional):** standardmäßig **aus**. Einschalten nur mit Tarif-Merkmal und eingetragener Freigabe
  (z. B. „Schulleitung, Datum“). Nur Einzelbilder bei einer Erschütterungswarnung oder auf Anforderung eines Admins,
  keine Videos, keine Speicherung auf dem Pi, Löschung nach 1–72 h (Standard 24 h), Ausschalten löscht sofort alle Bilder.
  Display und Stellplatz-Ansicht zeigen „Kamera aktiv“. Vor dem Einsatz sind Hinweisschild, Rechtsgrundlage und ggf.
  eine DSFA mit Schulleitung und Datenschutzbeauftragten zu klären – die Technik ersetzt diese Freigabe nicht.
- Kontodaten: Name, E-Mail, Rolle, Sprache; Sitzungen: IP und Browserkennung (Sicherheitszweck, max. 12 h).
- Betroffenenrechte: Datenexport (JSON, Art. 20), Konto löschen, Organisation vollständig löschen (Kaskade).
- Keine Tracking-/Werbe-Cookies; nur ein technisch notwendiges Session-Cookie.
- **Vor produktivem Einsatz durch den Betreiber zu klären:** Datenschutzerklärung und Impressum
  (Platzhalter auf der Landingpage), Auftragsverarbeitungsvertrag mit Kunden, Hosting-Standort,
  Verzeichnis der Verarbeitungstätigkeiten, ggf. DSFA im Schulkontext.

## Bekannte Grenzen / offene Punkte

- Keine Zahlungsanbindung – Beträge, Aufstellungen und Rechnungen werden berechnet, Zahlungen von Hand als bezahlt markiert ([abrechnung.md](abrechnung.md)).
- Ratenbegrenzung im Speicher je Prozess (bei mehreren Instanzen: Redis o. ä. nötig).
- SQLite für kleinen Betrieb; für viele Kunden auf PostgreSQL mit Row-Level-Security umstellen.
- Kein QR-Code für die 2FA-Einrichtung (Schlüssel + `otpauth://`-Link); WebAuthn/Passkeys als Erweiterung.
- Agent-Releases sind per Prüfsumme, aber noch nicht kryptografisch signiert.
- Kein externer Penetrationstest durchgeführt.
