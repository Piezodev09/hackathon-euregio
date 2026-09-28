# Operations: self-hosting on Proxmox

The platform (`server/`) is one FastAPI process with one SQLite database. It is designed to run in
your own network **without a domain and without a mail server** – e.g. in a small LXC container on a
Proxmox host at a school or municipality.

```
Proxmox host ── LXC "bikestation" (Debian 12, 2 vCPU, 1 GB RAM, 8 GB disk)
                 ├─ bike-station.service           HTTPS :443  (uvicorn, own CA)
                 ├─ bike-station-redirect.service  HTTP  :80 → HTTPS
                 ├─ avahi                          https://bikestation.local
                 └─ cron                           daily database backup
```

## 1. Install with one command (Proxmox host)

On the Proxmox host, as root, in a copy of the repository:

```bash
bash deploy/proxmox/create-lxc.sh                                    # DHCP, next free CT ID
bash deploy/proxmox/create-lxc.sh --ip 192.168.1.50/24 --gw 192.168.1.1   # static address
bash deploy/proxmox/create-lxc.sh --dry-run                          # only show the commands
```

The script checks `pveversion`, picks the next free container ID, downloads the newest Debian 12
template (`pveam`), creates an **unprivileged** container (2 vCPU, 1 GB RAM, 8 GB disk, `vmbr0`,
`nesting=1`, `onboot=1`), copies the repository into it with `pct push` (works with a private
repository and without git in the container) and runs `deploy/install-server.sh` inside.
Options: `--ctid`, `--hostname`, `--bridge`, `--vlan`, `--storage`, `--cores`, `--memory`, `--disk`,
`--ssh-key`, `--install-args "--no-ml"`. The container needs internet access for apt and pip
(offline: `--install-args "--wheelhouse /root/wheels"` with pre-downloaded wheels).

At the end you get:

```
   Portal:            https://192.168.1.50/app
   Also reachable:    https://bikestation.local (mDNS, in the LAN)
   CA certificate:    https://192.168.1.50/install/ca.crt
   CA fingerprint:    SHA-256 81:7F:D8:…
   FIRST STEP - create the admin account and your organisation:
   https://192.168.1.50/app#/setup?token=bss_…
```

Without Proxmox (VM, bare metal Debian 12): `sudo deploy/install-server.sh` in a checkout.
Docker is an alternative, see [`deploy/docker/`](../deploy/docker/).

## 2. First steps in the browser

1. Open the setup link. The browser warns about the certificate once: compare the fingerprint
   shown by the installer, then continue – or import `ca.crt` into the browser/OS trust store
   (recommended for kiosk screens).
2. Create the administrator and your organisation. The link works exactly once; afterwards the
   token file is deleted. (Alternative on the shell: `bike-station create-platform-admin --email …`.)
3. Set up two-factor sign-in (mandatory for the platform administrator).
4. Create a station, then *Set up gateway* and run the three commands on the Pi
   ([agent.md](agent.md)). The install script embeds and pins the platform CA.

### Landing page, legal pages and the live demo

- Operator details for `/legal/imprint` and `/legal/privacy`: `BIKE_OPERATOR_NAME`, `BIKE_OPERATOR_ADDRESS`
  (lines separated by `\n`) and `BIKE_CONTACT_EMAIL` in `server.env.local`. Until they are set, both pages show
  a clearly visible "template" warning.
- Live demo on the landing page: `bike-station demo --reset` creates a demo organisation with two stations,
  7 days of **simulated** history and a public display link; the platform keeps it alive with simulated readings
  (always labelled). Remove it with `bike-station demo-remove`.
- Demo requests from the landing page appear in *Platform → Demo requests* (no e-mail needed) and are deleted
  after 180 days.

## 3. Operation without a mail server

`install-server.sh` sets `BIKE_MAIL_BACKEND=none`. Nothing is ever sent; each attempt is audited
(`mail_not_sent`). Instead:

| Situation | How it works without e-mail |
|---|---|
| Invite a team member | *Team → Create invitation link*: link + QR code, shown only to the inviting admin, valid 72 h |
| Forgotten password | the sign-in page says "ask your administrator"; admins create a one-time **reset link** in *Team* (admins for lower roles, owners for everybody); the platform admin can do it for organisation owners (*Platform*) |
| New customer signs up | sign-up mode `approval`: the organisation is *pending* until the platform admin approves it in *Platform*; approval also counts as e-mail verification |

To use a mail server anyway, put the SMTP settings into `/etc/bike-station/server.env.local`
(see the comments there) and restart the service. `BIKE_SIGNUP=open|approval|closed` controls
self-registration.

## 4. What install-server.sh sets up

| Item | Location |
|---|---|
| Code + virtualenv | `/opt/bike-station/{server,web,agent,ml,deploy,venv}` (root-owned, read-only for the service) |
| Data (SQLite, setup token) | `/var/lib/bike-station` (user `bikestation`, 0750) |
| Configuration | `/etc/bike-station/server.env` (managed), `server.env.local` (your overrides) |
| Certificates | `/etc/bike-station/tls/ca.{key,crt}` (10 years), `server.{key,crt}` (825 days, SANs: all IPv4 addresses, host name, `<host>.local`, `bikestation.local`, `localhost`) |
| Services | `bike-station.service` (port 443, only capability `CAP_NET_BIND_SERVICE`, `ProtectSystem=strict`), `bike-station-redirect.service` (port 80), `bike-station-mdns.service` |
| Backups | `/etc/cron.d/bike-station` → `deploy/backup.sh` daily at 03:17 into `/var/backups/bike-station` (14 copies) |
| CLI | `bike-station --help` (runs `python -m app.cli` with the service configuration) |
| Demo AI model | trained on **simulated** data if none exists (clearly labelled in the portal); skip with `--no-demo-model` |

The script is idempotent: run it again to update the code. Data, data key and CA are kept; the
server certificate is renewed when the IP addresses change or it expires within 30 days.
**If the IP address changes, run it again** – better use a DHCP reservation or a static IP.

**Keep the data key** (`BIKE_DATA_KEY` in `server.env`) offline. Without it the 2FA and webhook
secrets cannot be decrypted after restoring a backup.

## 5. Start / stop / logs

| Action | Platform (in the container) | Pi |
|---|---|---|
| Status | `systemctl status bike-station` | `systemctl status bike-agent` · `sudo bike-agent status` |
| Logs | `journalctl -u bike-station -f` | `journalctl -u bike-agent -f` |
| Restart | `systemctl restart bike-station` | Portal → Gateways → *Restart* or `systemctl restart bike-agent` |
| Health | `curl --cacert /etc/bike-station/tls/ca.crt https://127.0.0.1/health` | – |

After a restart old states are not taken over: the Arduino first reports "unknown" until 2 s of
stable readings exist; the dashboard shows stale data as unknown.

## 6. Backup and restore

- Proxmox: `pct snapshot <ctid> before-demo` (LVM-thin/ZFS) and `vzdump <ctid> --mode snapshot`.
- Database: automatic daily backup (see above) or manually `deploy/backup.sh` (SQLite online backup +
  integrity check).
- Restore: `systemctl stop bike-station`, copy a backup to `/var/lib/bike-station/bike_station.db`,
  `chown bikestation: /var/lib/bike-station/bike_station.db`, `systemctl start bike-station`, check
  `/health`. **Test a restore at least once.**

## 7. Firewall

`deploy/nftables.conf.example` allows 80/443 and mDNS from the LAN and SSH from an admin network.
Adapt the networks, copy it to `/etc/nftables.conf` and `systemctl enable --now nftables`.

## 8. Deleting demo data

Customers delete their data themselves (portal → Organisation → Delete). For the demo:
`systemctl stop bike-station && deploy/delete-demo-data.sh`. Measurements and events are deleted
automatically after the plan period (Free 7, School 30, Pro 90 days), the audit log after 365 days.

## 9. Local development

`scripts/dev.sh` starts platform, demo customer and a simulated agent on one computer
(HTTP on 127.0.0.1, console mail – development only).
