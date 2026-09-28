# Agent for the Raspberry Pi – installation, pairing, operation

The **agent** (`agent/bikeagent/`) runs on the Raspberry Pi of every station. It reads the Arduino
over USB, sends the measurements encrypted to the platform and is managed centrally from the portal.

```
Portal: "Set up gateway"  ──►  pairing code (single use, 30 min)
                                        │
Pi:  curl …/install/agent.sh ─► checksum ─► sudo sh agent.sh --code XXXXX-XXXXX
                                        │
     script: download package + verify SHA-256 ─► user bike-agent ─► pairing ─► systemd service
                                        │
Agent ◄──── heartbeat (60 s): configuration, commands, updates ────► platform
      ─────  measurements (occupancy, vibration) ─────────────────►
```

## Setup (5 minutes)

Requirements: Raspberry Pi with **Raspberry Pi OS Bookworm** or newer (Python ≥ 3.11), network,
Arduino with the sketch from `firmware/` connected over USB.

1. Portal → **Stations** → station → **Settings** → **Set up gateway**.
2. Run the three commands shown there on the Pi:
   ```bash
   curl -fsSLO https://<platform>/install/agent.sh
   echo '<checksum>  agent.sh' | sha256sum -c -     # must print "OK"
   sudo sh agent.sh --code XXXXX-XXXXX
   ```
3. After about a minute the gateway is shown as **online** in the portal.

Script options: `--source simulator` (test without an Arduino), `--serial-port /dev/ttyUSB0`,
`--ca-file ca.crt`, `--name …`, `--no-systemd`, `--prefix/--etc-dir/--state-dir`. Without `--code`
the code is prompted for or read from `BIKE_ENROLL_CODE`.

### Self-hosted platform with its own CA (no domain)

A platform installed with `deploy/install-server.sh` uses its own small certificate authority. The Pi
does not trust it yet, so the portal shows slightly different commands:

```bash
curl -fsSLk -o agent.sh https://192.168.1.50/install/agent.sh   # -k: no trust yet …
echo '<checksum>  agent.sh' | sha256sum -c -                      # … the checksum pins the script
sudo sh agent.sh --code XXXXX-XXXXX
```

The checksum is shown in the portal over the admin's HTTPS session, so a manipulated script is
detected. The script **embeds the CA certificate** and uses it for the package download and the
pairing; the agent stores it (`/etc/bike-agent/ca.crt`, `ca_file` in `agent.json`) and verifies every
later connection against it – a man in the middle with any other certificate is rejected. The
one-line `curl | sh` variant is not offered in this mode, because it would skip the checksum.
Agents without the install script (Home Assistant add-on, Docker) pin the CA via its SHA-256
fingerprint (`--ca-fingerprint`, shown in the portal).

The one-line command (`curl … | sudo sh -s -- --code …`) is more convenient but does not verify the
script first.

## What the script does

| Step | Details |
|---|---|
| Checks | root, Python ≥ 3.11, HTTPS URL (HTTP only for `localhost` or with `--allow-http`) |
| Packages | `python3-serial`, `ca-certificates`, `curl` (apt) |
| User | system user `bike-agent` without login, group `dialout` (USB serial) |
| Package | download `/install/agent.tar.gz`, **verify SHA-256 against the value built into the script** |
| Layout | `/opt/bike-agent/releases/<version>/{VERSION,bikeagent/}`, symlink `/opt/bike-agent/current` |
| Pairing | `python3 -m bikeagent enroll` – the code travels via an environment variable (not visible in the process list) |
| State | `/var/lib/bike-agent/agent.json`, mode `0600`, contains the device token |
| Service | `bike-agent.service`, hardened (`NoNewPrivileges`, `ProtectSystem=strict`, no capabilities, tty devices only) |
| Helper | `sudo bike-agent status` · `sudo bike-agent rollback` |

The script can be run repeatedly; reinstalling with a new code pairs the device again.

## Management in the portal

- **Gateways** (navigation): every device of the organisation with status, version and health.
- **Health**: Arduino connected, simulated source, CPU temperature, buffered messages, free disk
  space, uptime, last error. *Offline* after 3 minutes without a heartbeat.
- **Commands** (executed with the next heartbeat): *Restart*, *Renew token*, *Update*. There is
  deliberately **no** way to run arbitrary commands.
- **Revoke**: the token becomes invalid immediately, the device cannot send data any more.
- **Install updates automatically** (per station, default: on).
- **Configuration**: added or removed spaces reach the agent with the next heartbeat.

## Security

| Topic | Implementation |
|---|---|
| Pairing code | 10 characters (~50 bit), single use, valid 30 min, stored only as a hash, rate-limited endpoint |
| Device token | 256 bit, stored only as a hash on the platform, valid for **one** station only |
| Token rotation | automatically every 30 days and on request; the old token stays valid for 15 min (no lock-out if the response is lost) and becomes invalid as soon as the new one is used |
| Transport | HTTPS with certificate verification (own CA possible); downloads only from the own platform |
| Updates | checksum delivered over the authenticated channel; safe extraction (known files only, no paths/links); version check; no downgrade |
| Rollback | if a new version starts 3 times without a successful heartbeat, the agent switches back to the previous one; manually: `sudo bike-agent rollback` |
| Privileges | own user, writes only to `/var/lib/bike-agent` and `/opt/bike-agent` |
| Package | built reproducibly (same content → same checksum), checksums at `/install/agent.sha256` |

Known limitation: the checksum protects integrity relative to the platform. Signing releases with an
offline key (e.g. Ed25519) would be the next step so that even a compromised platform could not
push updates.

## Rolling out a new agent version (operator)

1. Change `agent/bikeagent/`, run the tests (`cd agent && python3 -m pytest -q`).
2. Increase the version in `agent/VERSION`.
3. Restart the platform – the package is built at start-up.
4. Agents with automatic updates update themselves with the next heartbeat; others show
   "update available" and can be updated with one click.

## Troubleshooting

| Symptom | Check |
|---|---|
| Gateway stays offline | `systemctl status bike-agent`, `journalctl -u bike-agent -f`; network/firewall to the platform (port 443) |
| "Arduino ✗" | USB cable, `ls /dev/ttyACM* /dev/ttyUSB*`, port in `sudo bike-agent status`; pair again with `--serial-port` |
| "token rejected" | device revoked in the portal? Pair again: create a new code, `sudo sh agent.sh --code …` |
| Pairing fails | code expired/used → create a new one; the Pi's clock does not matter |
| Update hangs | `sudo bike-agent rollback`, then `sudo systemctl restart bike-agent` |

## Development without a Pi

`scripts/dev.sh` starts the platform with a demo customer and pairs a local agent with the built-in
simulator. The device appears in the portal under *Gateways*; commands and updates can be tried
there. `scripts/dev.sh --interactive` pipes the keyboard-controlled simulator into the agent
(`python3 -m bikeagent.simulator | python3 -m bikeagent run --source stdin`).
