# Offline kit – the demo runs without internet

Everything the demo needs runs in the local network of our own travel router: platform (Proxmox LXC),
portal, kiosk, agents, Home Assistant, MQTT and webhooks to Home Assistant. Venue Wi-Fi is a bonus,
never a dependency.

```
travel router (own SSID, DHCP reservations, no uplink needed)
 ├─ Proxmox mini PC / laptop ── LXC "bikestation"  https://192.168.8.10  (+ NTP for the LAN)
 ├─ Pi 1 + Arduino (station "Schoolyard")          192.168.8.21
 ├─ Pi 2 or Docker agent (station "Bike shed")     192.168.8.22
 ├─ Home Assistant (optional, with Mosquitto)      192.168.8.30
 ├─ kiosk screen (browser, CA imported)
 └─ presenter laptop, phone
```

## What needs internet – and how we avoid it

| Needs internet | Offline solution |
|---|---|
| `apt`/`pip` during installation | install everything at home; the Pi SD image already contains `python3-serial`, `python3-paho-mqtt`, `curl`, `ca-certificates` (then `agent.sh` needs no download from the internet) |
| Building the Home Assistant add-on | install it at home; at the venue only enter the pairing code |
| Time (NTP) – **TLS fails if the Pi's clock is wrong** | the Proxmox host serves NTP to the LAN (`chrony`: `allow 192.168.8.0/24`), Pis point to it (`/etc/systemd/timesyncd.conf`: `NTP=192.168.8.10`); check `timedatectl` on every Pi |
| Teams/Slack/Discord webhooks, phone push via the HA cloud | use a **Home Assistant webhook** in the LAN (below) and show the notification in the HA app/dashboard; Teams only if venue internet works |
| Fonts, scripts, maps | nothing external: fonts are self-hosted, no CDN, no trackers |

## Checklist – hardware

- [ ] Travel router (e.g. GL.iNet) + power supply, SSID/password printed, DHCP reservations set
- [ ] Proxmox mini PC/laptop + power supply; LXC autostart on; snapshot `before-demo`
- [ ] Pi 1 + power supply (official 5 V/3 A or 5 V/5 A for Pi 5) + SD card
- [ ] **Second SD card with an identical, tested image** (label it)
- [ ] Pi 2 (or the Docker agent on the Proxmox host) for the "gateway offline" moment
- [ ] Arduino with the sketch + **spare Arduino, already flashed** with the same sketch and slot ids
- [ ] Spare sensors (1 × presence, 1 × vibration), jumper wires, spare LEDs + resistors
- [ ] USB cables (2 × data-capable, labelled), USB hub if needed
- [ ] **USB power meter** (for the energy figures, plan 11) + note sheet
- [ ] Kiosk screen/tablet + stand, HDMI/USB-C adapters for the presenter laptop
- [ ] Multi-socket extension lead, cable duct / gaffer tape (no trip hazards), cable ties
- [ ] Demo object (bike or wheel on a stand), printed labels "free / occupied / fault" per space
- [ ] Printed QR code of the kiosk link (in case the kiosk screen fails)

## Checklist – software (at home, 1 day before)

- [ ] `cd server && python3 -m pytest -q` and `cd agent && python3 -m pytest -q` green on the demo commit
- [ ] Platform updated in the LXC (`sudo deploy/install-server.sh` again), `/health` ok
- [ ] Owner account with 2FA; operator account for the Operator; recovery codes printed and stored safely
- [ ] CA certificate imported on kiosk, presenter laptop and phone (no certificate warning on stage)
- [ ] Both gateways paired, *Install updates automatically* **off** during the event (no surprise updates)
- [ ] Webhook "Caretaker phone" → Home Assistant webhook, **Send test** delivered
- [ ] Backup video recorded (full 7-minute run, with sound) on two devices + USB stick
- [ ] Latency measured 10× (stable sensor change → kiosk), result written into the demo script
- [ ] Energy measured with the USB power meter (Pi idle/busy, Arduino + sensors), written into
      [firmware/README.md](../../firmware/README.md#energy-plan-11) – replaces the estimate

### Home Assistant webhook in the LAN (push without the cloud)

Webhook in the portal: *Integrations → Webhooks*, type *Generic JSON (signed)*,
URL `http://192.168.8.30:8123/api/webhook/bike-station-caretaker` (allowed because the platform is
self-hosted with `BIKE_WEBHOOK_ALLOW_PRIVATE=1`). Automation in Home Assistant:

```yaml
alias: Bike station → caretaker
triggers:
  - trigger: webhook
    webhook_id: bike-station-caretaker
    allowed_methods: [POST]
    local_only: true
actions:
  - action: persistent_notification.create
    data:
      title: Bike station
      message: "{{ trigger.json.text }}"
  - action: notify.notify          # phone push; needs the companion app (local push or internet)
    data:
      message: "{{ trigger.json.text }}"
```

The `text` field already contains the careful wording ("… a suspicion, not proof – please check on
site"). For production, verify `X-BikeStation-Signature` in a receiver that can (see
[api.md](../api.md#webhooks)); Home Assistant's webhook trigger does not check signatures, which is
acceptable only inside the closed demo LAN.

## Dry-run plan

| When | What | Done when |
|---|---|---|
| T-7 days | full build at home, both gateways, all 7 moments twice with a stopwatch | every moment within its time box |
| T-3 days | run from a cold start (everything unplugged) with the checklist only; swap SD card and Arduino once | cold start ≤ 10 min, swaps ≤ 2 min |
| T-1 day | record the backup video; measure latency and energy; freeze the commit (tag) | video on 3 media, values in the docs |
| T-2 h | set up at the venue on our own router; `timedatectl` on all Pis; `pct snapshot … before-demo` | all gateways *online*, test webhook delivered |
| T-30 min | checklist in [demo-script.md](demo-script.md#before-the-jury-arrives-t-30-min) | – |
| After each run | acknowledge warnings, *Revoke* the gateway paired live, re-create the spare pairing code, reset spaces | next run starts clean |

## Plan B

| Failure | Fallback |
|---|---|
| Proxmox host dead | platform on the presenter laptop via Docker: `docker compose -f deploy/docker/compose.yaml up -d` (images built beforehand), gateways re-paired with the laptop's IP – or backup video |
| Pi 1 dead | second SD card in Pi 2, or the Home Assistant add-on / Docker agent with the Arduino plugged into that machine |
| Arduino/sensor dead | spare Arduino (same sketch); the portal shows "unknown" meanwhile – say why that is correct |
| Kiosk screen dead | phone/laptop via the printed QR code |
| Router dead | phone hotspot with the same SSID/password (prepare the hotspot name in advance) |
| Everything | backup video, and the screenshots in [screenshots/](screenshots/) |
