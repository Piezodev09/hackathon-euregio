# Competition package

Everything the jury and the team need for the final presentation.

| File | Purpose |
|---|---|
| [criteria-matrix.md](criteria-matrix.md) | every challenge requirement → file, test, screenshot, demo step; honest gaps |
| [demo-script.md](demo-script.md) | the 7-minute live demo, minute by minute, with fallbacks |
| [pitch-outline.md](pitch-outline.md) | slide structure, speaker phrases, likely jury questions |
| [offline-kit.md](offline-kit.md) | running everything in our own LAN: hardware/software checklists, dry-run plan, plan B |
| [jury-kurzfassung.md](jury-kurzfassung.md) | one-page summary in German for the jury |
| [screenshots/](screenshots/) | evidence screenshots (demo organisation, **simulated** data, clearly labelled in the UI) |

## Screenshots

| File | Shows |
|---|---|
| `01-landing.png` | landing page (hero; the live demo station follows directly below) |
| `02-station-live.png` | portal live view: spaces, suggestion, connection state, 24 h occupancy |
| `03-heatmap-week.png` | typical week (weekday × hour) with text summary |
| `04-kiosk.png` | kiosk display at the station (read-only link) with legend and privacy note |
| `05-phone.png` | the same view on a phone after scanning the kiosk QR code |
| `06-events.png` | events with acknowledgement, "simulated" labels, careful wording |
| `07-integrations.png` | read-only API key and a Home Assistant webhook |
| `08-station-de.png` | the station view in German |
| `09-security.png` | account & security: two-factor setup, active sessions with device and IP |

Re-create them on fresh demo data with [`scripts/capture-screenshots.mjs`](../../scripts/capture-screenshots.mjs)
(Playwright); [`scripts/check-a11y.mjs`](../../scripts/check-a11y.mjs) runs axe-core over the same pages.
