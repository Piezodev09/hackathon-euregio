# KI-Steckbrief: Erkennung auffälliger Bewegungen

**Fragestellung:** Weicht das Bewegungsmuster an einem belegten Platz auffällig vom normalen
Abstellen und Ausparken ab?

**Bedeutung einer Anomalie:** nur „Dieses Muster war in den bisherigen Messungen ungewöhnlich.“
Kein Diebstahlnachweis, keine Aussage über Personen.

## Merkmale (je Bewertung, Fenster 10 s)

| Merkmal | Beschreibung |
|---|---|
| `n_peaks` | Meldungen mit `vibration_score ≥ peak_threshold` |
| `n_active` | Meldungen mit `vibration_score > 0` |
| `max_score` | stärkster Ausschlag |
| `mean_active_score` | mittlere Stärke der aktiven Meldungen |
| `since_change_s` | Sekunden seit letztem Belegungswechsel (max. 600) |

Bewertet wird jede Meldung mit Vibration an einem belegten Platz. Training (`ml/`) und Betrieb
(`backend/app/anomaly.py`) nutzen **dieselbe** Funktion `features_at`.

## Verfahren

| | Baseline-Regel | KI |
|---|---|---|
| Logik | ≥ `min_peaks` (3) Ausschläge ≥ `peak_threshold` (300) in 10 s | Isolation Forest (scikit-learn, 200 Bäume) |
| Training | – | nur normale Läufe, nach Schonzeit |
| Schonzeit | 15 s nach Belegungswechsel keine Warnung | ebenso |
| Ort | API auf der VM | API auf der VM |
| Ausfall | – | Modell fehlt/defekt → „nicht verfügbar“, Belegung läuft weiter |

Beide laufen immer parallel. Welches die **sichtbare** Warnung erzeugt, steuert
`anomaly.alert_source` in `backend/config.toml`; das andere wird als `shadow`-Ereignis gespeichert.

## Daten aufnehmen (Plan 8.2)

1. Normale Vorgänge mehrfach: leer, einstellen, geparkt, leichtes Anstoßen, Nachbarplatz, ausparken.
2. Auffällige Vorgänge kontrolliert simulieren: wiederholtes starkes Bewegen des Demo-Objekts.
   Niemanden zu echtem Diebstahl oder Beschädigung anleiten.
3. Jeden Durchlauf mit Start/Ende notieren und exportieren:
   `python3 ml/export_windows.py --db … --slot A --since … --until … --label normal --run-id r01`
4. Trainieren und vergleichen: `python3 ml/train.py ml/data/recorded.csv`
   → Aufteilung nach ganzen Läufen, Ergebnis in `ml/report.md`, Modell in `ml/models/`.
5. API neu starten, damit das Modell geladen wird (`/health` → `ai_model_available: true`).

## Ergebnis

| Datenstand | Läufe (Test) | Regel: erkannt / Fehlalarm | KI: erkannt / Fehlalarm | Entscheidung |
|---|---|---|---|---|
| Simuliert (Pipeline-Probe, 28.09.) | 54 (9 auffällig, 45 normal) | 9/9 · 4/45 | 9/9 · 4/45 | Regel bleibt sichtbar |
| Echte Aufnahmen | | | | |

Die simulierten Zahlen belegen nur, dass die Pipeline funktioniert – nicht die Wirksamkeit.

**Go/No-Go:** Erzeugt die KI mehr Probleme als die Regel, bleibt `alert_source = "rule"`; die KI wird
als demonstrierte, aber noch nicht ausreichend zuverlässige Forschungsfunktion erklärt.

## Bekannte Grenzen

- Wenige Hackathon-Messungen reichen nicht für belastbare Aussagen über echte Diebstähle.
- Neues Fahrrad, vorbeigehende Personen, Wind, Nachbarplätze oder Ausparken können Fehlalarme auslösen.
- Ein rein digitaler Vibrationssensor liefert nur Impulszahlen – die Stärke-Merkmale sind dann grob.
