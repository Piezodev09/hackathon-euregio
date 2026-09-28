# Betriebshinweise

## Debian-VM auf Proxmox

1. VM anlegen (Vorschlag: Debian 12, 1 vCPU, 1 GB RAM, 8 GB Disk) – Netz/VLAN mit IT abstimmen.
2. Repo nach `/opt/smart-bike-station` kopieren (ohne Tokens!).
3. `sudo /opt/smart-bike-station/deploy/install-vm.sh` – erzeugt Benutzer, venv, Zufallstokens,
   Demo-Zertifikat (selbst signiert, 30 Tage) und startet `bike-api` auf Port 8443.
4. Firewall: `/etc/nftables.conf.bike-example` an echte Netze anpassen, als `/etc/nftables.conf`
   übernehmen, `systemctl enable --now nftables`.
5. Prüfen: `curl -k https://localhost:8443/health`

KI-Modell übernehmen: `ml/models/vibration_iforest.joblib` nach
`/opt/smart-bike-station/ml/models/` kopieren, `systemctl restart bike-api`.

## Raspberry Pi

1. Repo nach `/opt/smart-bike-station`, dann `sudo deploy/install-pi.sh`.
2. `/etc/bike-gateway/config.toml`: API-URL, `ca_file` (Zertifikat der VM, `server.crt`), Serieller Port.
3. `/etc/bike-gateway/gateway.env`: Geräte-Token aus `/etc/bike-station/api.env` der VM.
4. `sudo systemctl enable --now bike-gateway`

## Start / Stopp / Neustart

| Aktion | VM | Pi |
|---|---|---|
| Status | `systemctl status bike-api` | `systemctl status bike-gateway` |
| Logs | `journalctl -u bike-api -f` | `journalctl -u bike-gateway -f` |
| Neustart | `systemctl restart bike-api` | `systemctl restart bike-gateway` |

Nach einem Neustart gelten alte Zustände nicht automatisch: Der Arduino meldet zunächst
„unbekannt“, bis 2 s stabile Messungen vorliegen; das Dashboard zeigt veraltete Daten als unbekannt.

## Backup und Wiederherstellung

- VM: Proxmox-Snapshot oder `vzdump` vor der Abschlussdemo (nach Freigabe).
- Datenbank: `deploy/backup.sh` (SQLite `.backup` + Integritätsprüfung).
- Wiederherstellen: `systemctl stop bike-api`, Sicherung nach `/var/lib/bike-station/bike_station.db`
  kopieren, `chown bikestation:`, `systemctl start bike-api`, `/health` prüfen.
  **Wiederherstellung mindestens einmal praktisch testen.**

## Demodaten löschen

`sudo -u bikestation deploy/delete-demo-data.sh` – löscht Messungen, Ereignisse und Audit-Log.
Rohmesswerte werden ohnehin nach `retention.measurements_max_age_h` (72 h) automatisch gelöscht.

## Lokale Entwicklung

`scripts/dev.sh` startet API, Simulator und Gateway auf einem Rechner (ohne TLS, nur lokal).
