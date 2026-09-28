# Design – Smart Bicycle Box (ein Stellplatz)

Entwurf für **genau einen vorne offenen Fahrradstellplatz** zur Schul-Demonstration.

| Datei | Inhalt |
|---|---|
| `smart-bicycle-box-prototyp.html` | Eigenständiger Klick-Prototyp (HTML/CSS/JS, kein Backend). Einfach im Browser öffnen. |
| `tokens.json` | Design-Tokens (Farben Hell/Dunkel, Schrift, Abstände, Radien). |

**Wichtig:** Der Prototyp zeigt ausschließlich **SIMULATION / DEMODATEN**. Er ist mit keiner Hardware
und keinem Server verbunden. Anwendung, Sensoranbindung, Dashboard-Backend und KI-Funktion sind noch
nicht implementiert; eingerichtet sind nur Edge-VM und App-VM als Infrastruktur auf Proxmox.

Kernregel: FREI oder BELEGT nur aus einer gültigen, höchstens 30 s alten Messung (Vorschlag).
Fehlende, veraltete oder fehlerhafte Messungen ergeben immer **STATUS UNBEKANNT** – nie automatisch FREI.

Präsentation: Tasten `1` / `2` / `3` schalten FREI / BELEGT / STATUS UNBEKANNT; Störungen
(veraltete Messung, Sensorfehler, Verbindungsabbruch, Erschütterung) lassen sich per Klick vorführen.

Alle Maße, Bauteile und Preise im Abschnitt „Stellplatz-Konzept“ sind Vorschläge und vor dem Bau zu prüfen
(Liste im Abschnitt „Vor dem Bau prüfen“).

Das Design ist auf die gesamte Weboberfläche übertragen: `web/static/css/tokens.css` (aus `tokens.json`
erzeugt) und `web/static/css/components.css` (identisch mit den Komponenten im Prototyp).
