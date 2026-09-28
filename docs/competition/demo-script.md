# Demo script – 7 minutes, seven moments

Roles: **Presenter** (speaks, drives the laptop), **Operator** (hardware at the station, second
laptop), optionally a **Stagehand** (cables, phone). Everything runs in the local network of the
travel router – no internet needed ([offline-kit.md](offline-kit.md)).

Timings below are the real defaults of the system: 2 s debounce, 2 s polling, agent watchdog 15 s,
"stale" after 30 s, gateway offline after 3 min without heartbeat (checked every minute).

## Before the jury arrives (T-30 min)

- [ ] Proxmox host, travel router, Pi 1 (station "Schoolyard", Arduino with 3 spaces), Pi 2 or the
      Docker agent (station "Bike shed", simulated) powered and **online** in *Gateways*.
- [ ] `pct snapshot <ctid> before-demo` done; `bike-station demo --reset` only if the demo data is used.
- [ ] Kiosk screen shows `/display#…` of "Schoolyard" (CA imported, full screen, EN).
- [ ] Portal open on the presenter laptop (signed in as owner with 2FA), tabs: *Station*, *Events*,
      *Gateways*, *Integrations*, *Audit log*; browser zoom 125 %.
- [ ] Webhook "Caretaker phone" (generic → Home Assistant webhook, or Teams if internet is available)
      subscribed to *Unusual movement*, *Gateway offline*, *Gateway back online*; **Send test** → "delivered".
- [ ] Phone of the Stagehand: Home Assistant companion app open (or the HA dashboard on a tablet).
- [ ] Home Assistant add-on "Smart Bike Station agent" **installed** (the build needs internet –
      done at home), not yet configured; a fresh pairing code is created live in moment 4.
- [ ] Demo object (bike or wheel on a stand) parked **outside** space A; all spaces free.
- [ ] Backup video ready in a second tab ([offline-kit.md](offline-kit.md#plan-b)).

**T-0 (unnoticed, before the first sentence):** the Stagehand unplugs the **power of gateway 2**
("Bike shed"). Its *gateway offline* push will arrive 2–4 minutes later – we pick it up in moment 3.

## 0:00 – 0:30 · Hook

> "Every morning 300 pupils look for a free bike space. Which one is free right now? The station
> knows – to the second, and it never guesses."

Point at the kiosk: 3 of 3 free, suggestion "Space A".

## 0:30 – 1:30 · Moment 1: park a bike – kiosk and phone in under 5 s

1. Stagehand scans the QR code on the kiosk with the phone → the same live view on the phone.
2. Operator parks the demo object in space A.
3. Presenter counts aloud: kiosk and phone switch to **A occupied**, the suggestion jumps to **B**
   (target ≤ 5 s: 2 s debounce + upload + 2 s polling – say the measured value from the dry run).

> "No app, no login: the kiosk link is read-only, and the phone just scanned it."

Evidence: screenshots 04/05, criteria 1, 3, 4.

## 1:30 – 2:30 · Moment 2: shake – a warning, and a push

1. Operator shakes the parked object firmly for ~5 s (after the 15 s grace period since parking).
2. Portal: warning banner "Unusual movement" for space A, the kiosk marks the space with ⚠; the Stagehand's phone
   buzzes (Home Assistant via webhook, or Teams).
3. **Say it clearly:** "This is a *suspicion*, not proof. We measure vibration – no camera, no
   names. Somebody should take a look."
4. Presenter clicks **Acknowledge** (operator role) – the banner disappears, the audit log records who.

Evidence: screenshot 06, criteria 5, S `test_T05_T06_alert_after_grace_but_not_while_parking`.

## 2:30 – 3:30 · Moment 3: pull the plug – "unknown", never "free"

1. Operator pulls the **Arduino's USB cable** at Pi 1.
2. Within ~15 s every space turns **"? unknown – sensor fault"**: "Available spaces: 0 of 3" – the station
   never claims a space is free when it cannot know. *Gateways* shows **Arduino ✗**.
3. Plug it back in → after 2 s of stable readings the real states return.
4. Meanwhile (2–4 min after T-0) the phone buzzes: **"Gateway offline at Bike shed: no contact for
   more than 3 minutes. Spaces show as unknown."** – exactly one message per outage. The Stagehand
   powers gateway 2 on again; its return later closes the warning automatically.

> "Four layers make sure of this: Arduino, Pi, platform and even the browser – if the connection
> drops, the page itself switches to unknown after 30 seconds."

Evidence: criteria 6, S `test_no_data_is_unknown_never_free`, `test_gateway_offline_raises_one_event_and_online_info`.

## 3:30 – 4:30 · Moment 4: a new gateway in 60 seconds

1. Portal → station "Bike shed" → *Settings* → **Set up gateway** → pairing code on screen.
2. Either **Home Assistant**: open the pre-installed add-on, paste `platform_url`, the pairing code and
   the CA fingerprint (the dialog's *Home Assistant or Docker* section shows all three with copy
   buttons), *Start*; or **Pi**: run the
   three displayed commands (download, `sha256sum -c` → OK, `sudo sh agent.sh --code …`).
3. After ≤ 60 s the gateway appears as **online**; in Home Assistant the station shows up as a
   device: occupancy and movement warning per space, free spaces, gateway health (MQTT discovery).

> "No token copied by hand, the code works once and expires after 30 minutes, and the gateway
> pins our own certificate authority – no domain, no cloud."

Evidence: screenshot 07, [agent.md](../agent.md), A `test_first_start_pairs_and_takes_mqtt_from_supervisor`.

## 4:30 – 5:15 · Moment 5: the whole platform in one Proxmox container

1. Switch to the Proxmox web UI: one unprivileged LXC "bikestation", 2 vCPU, 1 GB RAM.
2. Show the snapshot list (`before-demo`) – "if anything goes wrong, one click back".
3. One sentence on setup: `bash deploy/proxmox/create-lxc.sh` → URL, CA fingerprint, one-time
   setup link. Without a domain, without a mail server.

Evidence: criteria 2, 8; [operations.md](../operations.md).

## 5:15 – 6:00 · Moment 6: AI – honestly compared

1. Station view → card *Movement detection*: "Visible warnings from: rule", "AI model available".
2. Show the comparison table (from [ai-factsheet.md](../ai-factsheet.md) / `ml/report.md`):
   same test runs, split by complete runs – rule 9/9 detected with 4/45 false alarms, AI 9/9 with 5/45.
3. **Say it clearly:** "On our (simulated) data the AI is *not* better – so the rule stays visible and
   the AI runs in the shadow. With real recordings we switch only if it wins."
4. *Events → Show comparison results*: the shadow decisions are there for anyone to check.

Evidence: criteria 7.

## 6:00 – 6:45 · Moment 7: security and languages – by keyboard only

1. Hands off the mouse: `Tab` to the language switch → **DE** → **NL** → **EN**; the page, the
   warning texts and the kiosk (its own switch) change language.
2. *My account & security*: 2FA active, sessions with device and IP; *Audit log*: the
   acknowledgement from moment 2 and the pairing from moment 4 with user, time and IP.
3. One sentence: "Passwords with scrypt, 2FA, strict tenant isolation, signed webhooks, read-only
   API keys – and no personal data at all at the station."

Evidence: screenshots 08, 09; criteria 10, 11, 13, 14.

## 6:45 – 7:00 · Close

> "Every space, live. Unknown is never free. A warning is a suspicion, not an accusation. Runs on
> hardware you already have – in your own network. Thank you."

## If something fails

| Problem | Reaction (say it openly) |
|---|---|
| Kiosk does not switch within 5 s | show the timestamp on the space; mention the measured dry-run value; continue |
| No push on the phone | show the *Events* page and the webhook's *Last delivery* in *Integrations* |
| Arduino does not come back after moment 3 | that *is* the point: it stays "unknown"; swap to the spare Arduino later |
| Pairing takes longer than 60 s | continue with moment 5, come back when the gateway is online |
| Network down | switch to the backup video ([offline-kit.md](offline-kit.md#plan-b)) |
