# Operations

## Platform

The platform (`server/`) is a single FastAPI process with one SQLite database. It runs in a
Debian 12 LXC container or VM on Proxmox, served by uvicorn with TLS (see
`deploy/systemd/bike-api.service` and `deploy/server.env.example`).

In `production` the platform refuses to start with an insecure configuration.
**Back up the data key `BIKE_DATA_KEY` offline** – without it the 2FA secrets are useless after a
restore (users would have to set up 2FA again).

## Raspberry Pi (one per station)

Portal → station → Settings → *Set up gateway*, then run the three commands shown on the Pi.
Details, options and troubleshooting: [agent.md](agent.md).

## Start / stop / restart

| Action | Platform | Pi |
|---|---|---|
| Status | `systemctl status bike-api` | `systemctl status bike-agent` · `sudo bike-agent status` |
| Logs | `journalctl -u bike-api -f` | `journalctl -u bike-agent -f` |
| Restart | `systemctl restart bike-api` | Portal → Gateways → *Restart* or `systemctl restart bike-agent` |

After a restart old states are not taken over: the Arduino first reports "unknown" until 2 s of
stable readings exist; the dashboard shows stale data as unknown.

## Backup and restore

- Container/VM: Proxmox snapshot or `vzdump` before the final demo.
- Database: `deploy/backup.sh` (SQLite online backup + integrity check, keeps 14 copies).
- Restore: `systemctl stop bike-api`, copy the backup to `/var/lib/bike-station/bike_station.db`,
  `chown bikestation:`, `systemctl start bike-api`, check `/health`.
  **Test a restore at least once.**

## Deleting demo data

Customers delete their data themselves (portal → Organisation → Delete). For the demo:
`deploy/delete-demo-data.sh`. Measurements and events are deleted automatically after the plan
period (Free 7, School 30, Pro 90 days), the audit log after 365 days.

## Local development

`scripts/dev.sh` starts platform, demo customer and a simulated agent on one computer (no TLS, local only).
