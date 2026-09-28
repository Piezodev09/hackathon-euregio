# Abrechnung: Parkgebühren und Lizenzmodell

Es gibt zwei getrennte Geldflüsse. **Keiner ist an einen Zahlungsanbieter angebunden.** Die Plattform
berechnet die Beträge und erzeugt Aufstellungen, Rechnungen und CSV-Exporte. Bezahlt wird außerhalb der
Plattform (Überweisung oder bar), anschließend wird die Zahlung im Portal von Hand als „bezahlt“ markiert.

```
Radfahrende ──Parkgebühr──► Organisation (Schule, Betreiber der Stellplätze) ──Lizenz──► Plattform-Betreiber
             (NFC-Karte, Tarif der Organisation)                               (pro Stellplatz und Tag)
```

## 1. Parkgebühren (Organisation → Radfahrende)

Ablauf: Karte an den Leser halten → **eingecheckt**. Dieselbe Karte erneut an den Leser halten → **ausgecheckt**,
der Betrag wird nach dem Tarif berechnet und am Parkvorgang festgeschrieben. Der Tarif wird beim Einchecken
kopiert, eine spätere Tarifänderung ändert laufende Vorgänge also nicht.

| Einstellung | Bedeutung |
|---|---|
| Abrechnung | `kostenlos` · `pauschal je Vorgang` · `je angefangene Stunde` · `je angefangenen Kalendertag` (Europe/Berlin) |
| Preis | in Euro (intern in Cent) |
| Freiminuten | kürzere Vorgänge kosten nichts; bei „je Stunde“ zählen die Stunden erst nach den Freiminuten |
| Tageshöchstbetrag | optional, Obergrenze je Kalendertag |

Der Tarif gilt für die ganze Organisation (Portal → **Parkgebühren**). Einzelne Stellplätze können einen
eigenen Tarif bekommen (Stellplatz → Einstellungen → Betrieb & Tarif).

Beispiele (mit `backend/app/billing.py::compute_fee` nachgerechnet):

| Tarif | Parkdauer | Betrag |
|---|---|---|
| 0,50 € je Tag, 15 Freiminuten | Mo 07:40–13:10 | 0,50 € |
| 0,50 € je Tag, 15 Freiminuten | Mo 07:40–07:50 | 0,00 € (Freiminuten) |
| 0,50 € je Tag, 15 Freiminuten | Mo 17:00 – Mi 08:00 | 1,50 € (3 Kalendertage) |
| 0,20 € je Stunde, 30 Freiminuten, max. 1,00 €/Tag | Mo 07:40–09:40 | 0,40 € |
| 0,20 € je Stunde, 30 Freiminuten, max. 1,00 €/Tag | Mo 07:40–13:10 | 1,00 € (Höchstbetrag) |

**Monatsaufstellung** (Portal → Parkgebühren): Summe je Karte und Monat, CSV-Export, Status offen/bezahlt.
Enthält der Monat simulierte Vorgänge, ist die Aufstellung als **SIMULATION / DEMODATEN** gekennzeichnet.

Sonderfälle:

| Fall | Verhalten |
|---|---|
| unbekannte Karte | wird als „wartet auf Freigabe“ angelegt, kein Check-in; Admin benennt und gibt frei |
| gesperrte Karte | abgelehnt |
| anderes Fahrrad ist bereits eingecheckt | abgelehnt (`occupied_by_other`) |
| Karte ist an einem anderen Stellplatz eingecheckt | abgelehnt (`open_elsewhere`) |
| Wartungsmodus | abgelehnt, Display zeigt „AUSSER BETRIEB“ |
| Plattform > 60 s nicht erreichbar | Vorgang wird verworfen statt verspätet gebucht |
| vergessenes Auschecken | Betreuer beenden den Vorgang im Portal (mit Gebühr) oder Admins stornieren ihn (ohne Gebühr) |

Datenschutz: Die Kartennummer (UID) wird nie im Klartext gespeichert, sondern nur als HMAC mit dem
Datenschlüssel der Plattform. Eine Karte trägt nur eine frei gewählte Bezeichnung (z. B. „Karte 7a-12“),
keinen Namen.

## 2. Lizenzmodell (Plattform-Betreiber → Organisation)

Die Organisation zahlt **je Stellplatz und Tag**. Bei kostenpflichtigen Tarifen kommt eine monatliche
Grundgebühr dazu. Grundlage sind die tatsächlich vorhandenen Stellplätze je Kalendertag (`usage_day`,
Maximum des Tages).

| Tarif | Grundgebühr / Monat | je Stellplatz und Tag | max. Stellplätze | Testphase | Funktionen |
|---|---|---|---|---|---|
| Free | – | – | 1 | – | Status, Anzeige, NFC, Stellplatz-Ansicht |
| Schule | 9,00 € | 0,20 € | 5 | 30 Tage | + Parkgebühren, Kamera, KI, Audit-Log |
| Pro | 29,00 € | 0,15 € | 50 | 30 Tage | wie Schule, längere Aufbewahrung |

Rechenbeispiele für einen vollen Monat mit 30 Tagen:

- Schule mit 5 Stellplätzen: 9,00 € + 5 × 30 × 0,20 € = **39,00 €** im Monat
- Pro mit 20 Stellplätzen: 29,00 € + 20 × 30 × 0,15 € = **119,00 €** im Monat

Alle Preise sind Richtwerte für die Demo, zzgl. MwSt.

Regeln:

- **Testphase ist kostenlos.** Solange sie läuft, fallen weder Stellplatz-Tage noch Grundgebühr an. Sie endet
  nach 30 Tagen oder früher, sobald der Plattform-Betreiber einen Vertrag einträgt.
- **Vertragspreise.** Der Plattform-Betreiber kann je Kunde einen eigenen Tagespreis, eine eigene Grundgebühr,
  eine Laufzeit und Notizen hinterlegen (Plattform → Rechnungen → *Lizenz bearbeiten*).
- **Rechnungen.** Die Vorschau zählt bis heute. *Festschreiben* erzeugt eine unveränderliche Rechnung mit
  Nummer `SBB-JJJJMM-NNNN` und Positionen. Ihr Status ist offen, bezahlt oder storniert. CSV-Export für die Buchhaltung.
- Kunden sehen unter **Tarif & Nutzung** ihren Lizenzstatus, die Resttage, die bisherigen Stellplatz-Tage,
  den bisherigen Monatsbetrag und ihre Rechnungen.
- Tarifmerkmale werden serverseitig geprüft. Fehlt ein Merkmal im Tarif, antwortet die API mit `402 plan_feature`.

## Noch nicht enthalten

Online-Zahlung (z. B. SEPA-Lastschrift oder Karte), Mahnwesen, Umsatzsteuer-Ausweis auf der Rechnung,
PDF-Rechnungen und Guthabenkonten je Karte (Vorschlag 12). Vor dem echten Einsatz ist außerdem zu klären,
ob die Schule überhaupt Gebühren erheben darf (Schulträger) und wie diese steuerlich zu behandeln sind.
