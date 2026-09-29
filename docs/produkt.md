# Smart Bicycle Box – Produktbeschreibung und Vermarktung

> Kurz gesagt: **Der Fahrradstellplatz, der ehrlich sagt, ob er frei ist.** Sensor, Display und Portal in einem:
> Radfahrende sehen sofort, ob ein Platz frei ist, checken per Karte ein und aus. Die Schule oder Gemeinde
> verwaltet Gebühren, Reservierungen und Berichte im Browser.

## 1. Das Problem

- Fahrradplätze an Schulen, Bahnhöfen und Firmen sind morgens voll oder falsch belegt, und niemand weiß es vorher.
- Bestehende „smarte“ Lösungen zeigen oft „frei“, obwohl der Sensor ausgefallen ist. Das kostet Vertrauen.
- Parkgebühren, Reservierungen und Ferienzeiten werden mit Zetteln und Excel verwaltet.
- Die Schul-IT kann Insellösungen nicht anbinden, und Datenschutzfragen bremsen jedes Projekt.

## 2. Die Lösung

| Baustein | Was er leistet |
|---|---|
| **Stellplatz** (vorne offen) | Präsenzsensor, Erschütterungssensor, NFC-Leser; eine Kamera ist optional |
| **Display oben vorne** | FREI · BELEGT · RESERVIERT · STATUS UNBEKANNT, immer mit Wort, Symbol und Farbe; zeigt die Rückmeldung beim Ein- und Auschecken |
| **Raspberry Pi (Gateway)** | Überträgt die Daten verschlüsselt und puffert sie. Fällt das Netz aus, zeigt das Display den Zustand direkt vom Sensor („OFFLINE“). |
| **Portal im Browser** | Live-Status, Karten, Gebühren, Guthaben, Reservierungen, Öffnungszeiten, Berichte, E-Mails, Team, API |
| **Stellplatz-Ansicht per QR** | Radfahrende sehen auf dem Handy Status, Preise und Öffnungszeiten und können ein Problem melden |

**Das Versprechen:** Ohne gültige, aktuelle Messung zeigt das System nie „frei“, sondern STATUS UNBEKANNT.
Eine Warnung ist ein Hinweis auf ungewöhnliche Bewegung, kein Diebstahlnachweis.

## 3. Zielgruppen und Nutzen

| Zielgruppe | Wichtigster Nutzen | Passende Funktionen |
|---|---|---|
| **Schulen** (Schulträger, Schulleitung, Hausmeister) | Ordnung auf dem Schulhof, sichere Fahrräder, kaum Verwaltungsaufwand | Schülerausweis als Karte (ohne Namen), Ferien als Sperrzeit, Wochenbericht für die Schulleitung, Anbindung der Schul-App |
| **Gemeinden & Bahnhöfe** | Faire Gebühren, weniger Fehlbelegung, weniger Beschwerden | Tages-/Stundentarif, Guthaben je Karte, QR-Aufkleber mit Preisen, „Problem melden“ |
| **Hochschulen & Unternehmen** | Plätze für Mitarbeitende und Gäste planbar machen | Reservierungen, bis 50 Stellplätze, Rollen, Webhooks für Facility-Management |

## 4. Funktionen nach Paket

| | **Free** | **Schule** | **Pro** |
|---|---|---|---|
| Preis | 0 € | 9 € / Monat + 0,20 € je Stellplatz und Tag | 29 € / Monat + 0,15 € je Stellplatz und Tag |
| Testphase | – | 30 Tage kostenlos | 30 Tage kostenlos |
| Stellplätze / Nutzer | 1 / 2 | 5 / 10 | 50 / 50 |
| Datenaufbewahrung | 7 Tage | 30 Tage | 90 Tage |
| Status, Display, Stellplatz-Ansicht, NFC-Check-in | ✔ | ✔ | ✔ |
| E-Mail-Warnungen, Öffnungs- und Sperrzeiten | ✔ | ✔ | ✔ |
| Parkgebühren, Monatsaufstellung, Guthaben je Karte | – | ✔ | ✔ |
| Reservierungen | – | ✔ | ✔ |
| Tages-/Wochen-/Monatsberichte (PDF/CSV, per E-Mail) | – | ✔ | ✔ |
| Statistiken (Auslastung, Heatmap, Parkdauer, Kartenleser) | 7 Tage | 30 Tage | 90 Tage |
| Mehrere Stellplätze an einem Raspberry Pi, USB-/PC-SC-Kartenleser, Karten anlernen | ✔ | ✔ | ✔ |
| Anlagen mit Großanzeige „3 von 10 frei“, öffentliche Status-Seite | ✔ | ✔ | ✔ |
| Karten-App für Radfahrende (freie Plätze, Guthaben, Verlauf; Link je Karte) | ✔ | ✔ | ✔ |
| Selbst-Reservierung in der App, Warteliste mit Push-Nachricht | – | ✔ | ✔ |
| API-Schlüssel und Webhooks | – | ✔ | ✔ |
| Kamera (optional, nach Freigabe), KI-Vergleich, Audit-Log | – | ✔ | ✔ |

Rechenbeispiel „Schule“ mit 5 Stellplätzen: 9 € + 5 × 30 × 0,20 € = **39 € im Monat**. Alle Preise sind Richtwerte
für die Demo und verstehen sich zzgl. MwSt. Bezahlt wird per Rechnung. Es gibt keine Kreditkarte und keinen Zahlungsanbieter.

## 5. Warum Smart Bicycle Box? (Unterscheidungsmerkmale)

1. **Ehrlicher Status.** Nie „frei“ ohne gültige, aktuelle Messung. Das ist technisch in jeder Schicht umgesetzt: Firmware, Gateway, Server, Browser.
2. **In Minuten startklar.** Registrieren ohne E-Mail-Bestätigung, Start-Tour, Beispiel-Stellplatz ohne Hardware, Raspberry Pi per Kopplungscode.
3. **Datenschutz ab Werk.** Karten nur als Hash, keine Namen nötig, Kamera standardmäßig aus, automatische Löschfristen, Datenexport per Klick.
4. **Offen für die Schul-IT.** API-Schlüssel mit Rechten, signierte Webhooks, Betrieb auf eigenen Servern (z. B. Proxmox) möglich.
5. **Barrierearm und mehrsprachig.** Deutsch, Niederländisch und Englisch; Zustände nie nur über Farbe; Bedienung per Tastatur.
6. **Faire Preise.** Bezahlt wird pro Stellplatz und Tag, also nur für Plätze, die es wirklich gibt.

## 6. Pitch

**In einem Satz:** Die Smart Bicycle Box ist ein Fahrradstellplatz mit Display, der ehrlich sagt, ob er frei ist,
und der Schule oder Gemeinde Karten, Gebühren, Reservierungen und Berichte im Browser abnimmt.

**In 30 Sekunden:**
„Jeden Morgen dasselbe: Fahrradplätze voll, Fahrräder quer, niemand weiß, wo noch Platz ist. Unser Stellplatz zeigt
oben groß FREI oder BELEGT. Weiß er es nicht sicher, sagt er ehrlich STATUS UNBEKANNT. Mit der Schulkarte checkt man
ein und aus. Die Schule legt Gebühren, Öffnungszeiten und Ferien fest, reserviert Plätze für Besuch und bekommt
jeden Montag einen Bericht. Ausprobieren dauert zwei Minuten, auch ohne Hardware.“

## 7. Vorführung in 5 Minuten (Ablauf)

1. **Landingpage** zeigen: Status-Beispiel umschalten (FREI → BELEGT → RESERVIERT → UNBEKANNT).
2. **Registrieren** („Kostenlos testen“). Man ist sofort angemeldet, die Start-Tour startet von selbst. Zwei oder drei Schritte zeigen, dann „Tour beenden“.
3. **Beispiel-Stellplatz anlegen.** Die Live-Ansicht zeigt Fahrräder, Check-in/-out und gelegentlich eine Warnung (SIMULATION).
4. **Reservieren**, dann die Kiosk-Anzeige öffnen: RESERVIERT mit Restzeit.
5. **Parkgebühren** auf „Guthaben“ stellen, die Beispiel-Karte aufladen und die Buchungen zeigen.
6. **Berichte**: Wochenbericht als PDF herunterladen.
7. **Integrationen**: API-Schlüssel erzeugen und den Beispiel-Befehl zeigen.
8. Wenn Hardware vorhanden ist: echten Stellplatz zeigen, Karte an den Leser halten, das Display wechselt.

## 8. Häufige Einwände und Antworten

| Einwand | Antwort |
|---|---|
| „Ist das eine Diebstahl-Erkennung?“ | Nein. Eine Warnung meldet nur ungewöhnliche Bewegung, sachlich und ohne Personenbezug. Das sagen wir überall so. |
| „Datenschutz? Kamera?“ | Karten werden nur als Hash gespeichert und brauchen keine Namen. Die Kamera ist optional, standardmäßig aus, nur mit eingetragener Freigabe, nur Einzelbilder und wird automatisch gelöscht. |
| „Was, wenn das Internet ausfällt?“ | Das Display am Pi zeigt weiter den Zustand vom Sensor (OFFLINE). Messungen werden gepuffert und nachgesendet. |
| „Müssen wir online bezahlen?“ | Nein. Die Lizenz wird per Rechnung bezahlt. Parkgebühren laufen als Monatsaufstellung oder als Guthaben, das z. B. bar im Sekretariat aufgeladen wird. |
| „Passt das zu unserer IT?“ | Ja. Der Betrieb auf eigenen Servern ist möglich, dazu API-Schlüssel und Webhooks. Die Schnittstellen sind im Portal erklärt. |
| „Wie viel Aufwand ist die Einrichtung?“ | Das Konto dauert 1 Minute, den Raspberry Pi koppelt man per Code in etwa 5 Minuten. Danach QR-Aufkleber drucken und Karten anlernen. |

## 9. Tonalität für Texte und Werbung

- **Ehrlich:** nichts versprechen, was nicht getestet ist. Simulationen immer als SIMULATION kennzeichnen.
- **Konkret:** Nutzen in Alltagssprache („weniger Chaos auf dem Schulhof“), nicht in Technik.
- **Nicht dramatisieren:** kein „Diebe erwischen“, kein „KI erkennt Diebstahl“.
- **Kurz:** Überschrift mit Nutzen, ein Satz Erklärung, ein klarer Knopf („30 Tage kostenlos testen“).

## 10. Ehrlicher Stand (für Gespräche)

| Teil | Stand |
|---|---|
| Plattform, Portal, Start-Tour, Gebühren, Guthaben, Reservierungen, Öffnungszeiten, Berichte, E-Mails, API/Webhooks | umgesetzt und automatisch getestet |
| Statistiken, Hilfe-Menü/Tour, Karten anlernen | umgesetzt und automatisch getestet |
| Anlagen-Großanzeige, Karten-App (PWA), Warteliste, Status-Seite | umgesetzt, automatisch und im Browser getestet |
| Push-Zustellung über echte Push-Dienste (Google/Mozilla/Apple) | nach Standard umgesetzt, live nicht getestet |
| Raspberry-Pi-Agent inkl. Offline-Anzeige, NFC, Kamera, mehrere Stellplätze je Pi, Hardware-Erkennung | mit Simulator bzw. gefälschten Gerätedaten getestet |
| USB-/PC-SC-Kartenleser, mehrere echte Arduinos an einem Pi, Kiosk-Autostart | Hardwaretest steht aus |
| Arduino mit echten Sensoren und NFC-Leser, echte Kamera | Hardwaretest steht aus |
| Online-Zahlung, PDF-Rechnungen mit Umsatzsteuer | nicht enthalten |

## 11. Nächste Schritte (Roadmap-Ideen)

- Pilot an einer Schule mit 2–5 Stellplätzen und echten Karten
- Schul-App-Anbindung als Referenzprojekt, veröffentlicht als Beispiel
- Online-Zahlung (SEPA/Karte) optional für Gemeinden
- Wegweiser zur nächsten Anlage mit freien Plätzen (Karte, ohne Standort des Handys)
- Ladeplätze für E-Bikes (Steckdose schalten, Ladegebühr)
