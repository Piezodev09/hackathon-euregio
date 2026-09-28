# Pitch outline

Slide structure for the final presentation (≈ 5 min of slides around the 7-minute live demo, or
the demo embedded as slide 4). One message per slide, large type, every number with its source.
Figures marked *estimate* must be said as estimates. The deck itself can be produced from this
outline.

| # | Slide | Core message (one sentence) | Content / visual | Evidence |
|---|---|---|---|---|
| 1 | **Title** | Every bike space, live – no more searching. | product name, logo, one photo of the station | `web/static/img/icon.svg` |
| 2 | **Problem** | At peak times people circle full bike parks; nobody knows which space is free, and bikes are left unsupervised. | a full school bike park at 07:55; "which space is free?" | – (describe the local situation; no invented statistics) |
| 3 | **Solution** | Small sensors per space show free spaces on a screen and every phone, suggest one and report unusual movement – as a suspicion, never as proof. | kiosk screenshot + phone | screenshots 04, 05 |
| 4 | **Live demo** | Seven moments in seven minutes. | the moments as a list, then switch to the demo | [demo-script.md](demo-script.md) |
| 5 | **How it works** | Arduino, Raspberry Pi and Proxmox – each with one clear job. | the chain sensor → Arduino → Pi → Proxmox LXC → browser (+ Home Assistant, webhooks) | [architecture.md](../architecture.md) |
| 6 | **Reliability** | "Unknown" is never shown as "free" – enforced in four places. | four layers, 15 s / 30 s timeouts, buffered upload, gateway-offline alarm | criteria 6, test names |
| 7 | **AI, honestly** | We compare AI and rule on the same runs – and today the rule wins, so it stays visible. | comparison table (simulated: rule 9/9 · 4/45, AI 9/9 · 5/45), shadow mode | [ai-factsheet.md](../ai-factsheet.md) |
| 8 | **Security & privacy** | No cameras, no names – and security of a professional SaaS. | 2FA, tenant isolation, pinned CA, signed webhooks, read-only API keys, audit log; data minimisation and export/deletion | [security-privacy.md](../security-privacy.md), screenshot 09 |
| 9 | **Accessible & multilingual** | Usable by everybody, in EN, DE and NL. | symbols + text instead of colour, keyboard, AA contrast, axe-core 0 serious findings | criteria 13, 14, screenshot 08 |
| 10 | **Sustainability** | Runs on hardware you already have, locally, with a few watts per station. | reuse of an existing Proxmox host, ~4 W per station (*estimate* until measured), dimmable LEDs, retention limits | [firmware/README.md](../../firmware/README.md#energy-plan-11) |
| 11 | **Business model** | Free to start, affordable for schools, self-hosted for municipalities. | plans: Free €0 (1 station, 4 spaces), School €19/month (5 stations, AI, audit log), Pro €79/month (50 stations); hardware starter kit ≈ €130 per station with 3 spaces (*estimate*); self-hosting on the municipality's Proxmox with one command | `server/app/plans.py`, landing page |
| 12 | **Integrations** | Fits into what schools and towns already run. | Home Assistant (add-on/MQTT), Teams/Slack/Discord, REST API, Docker | screenshot 07, [api.md](../api.md) |
| 13 | **Team** | *[names, roles, what each built – to be filled in by the team]* | photo | – |
| 14 | **Limits & next steps** | What is not proven yet – and how we will prove it. | AI on real recordings, latency/energy measured on site, native-speaker review, signed agent releases, pen test, pilot at one school | [criteria-matrix.md](criteria-matrix.md#honest-gaps-open-until-the-event) |
| 15 | **Close** | Every space, live. Unknown is never free. A warning is a suspicion, not an accusation. | QR code to the live demo / landing page | – |

## Speaker notes – phrases to use and to avoid

| Use | Avoid |
|---|---|
| "unusual movement – a suspicion, please check on site" | "theft detected", "thief" |
| "on simulated data the AI is not better than the rule" | "our AI detects thefts" |
| "about 4 W per station – an estimate until we measure it" | CO₂ savings without a measurement |
| "runs in your own network, no cloud needed" | "100 % secure" |

## Likely jury questions

| Question | Short answer |
|---|---|
| What happens when a sensor fails? | The space shows "unknown" within seconds – never "free"; a sensor fault event is logged; a silent gateway raises exactly one "gateway offline" alarm. |
| Is it GDPR-compliant? | No personal data at the station (no cameras, no names). Accounts are minimal, with export and deletion; self-hosting keeps everything on site. The operator still has to complete the privacy notice and imprint (templates at `/legal/*`) and conclude a data processing agreement. |
| Why not only AI? | Because it is not better yet on our data. We show the comparison and switch only when real recordings prove it. |
| How much does it cost? | Hardware ≈ €130 per station with 3 spaces (estimate); software free for one station, €19/month for schools, or self-hosted. |
| How long does installation take? | Platform: one command on Proxmox (a few minutes). Gateway: pairing code + one command or the Home Assistant add-on (~1 minute). |
| Can the school's IT run it? | Yes: one LXC, HTTPS with its own CA, daily backups, snapshots, no domain and no mail server needed. |
