# Betriebshinweise

## Debian-VM auf Proxmox (Plattform)

1. VM anlegen (Vorschlag: Debian 12, 2 vCPU, 2 GB RAM, 16 GB Disk). DNS-Name, Zertifikat und Firewall mit der IT klären.
2. Repo nach `/opt/smart-bike-station` kopieren.
3. `sudo /opt/smart-bike-station/deploy/install-vm.sh` – legt Benutzer, venv, **Datenschlüssel** und
   (falls nötig) ein Demo-Zertifikat an und startet `bike-api` auf Port 8443.
4. `/etc/bike-station/api.env` anpassen: `BIKE_BASE_URL` (https!), `BIKE_ALLOWED_HOSTS`, SMTP-Zugang.
   In `production` verweigert die Plattform den Start bei unsicherer Konfiguration.
5. **Datenschlüssel `BIKE_DATA_KEY` offline sichern** – ohne ihn sind die 2FA-Geheimnisse nach einer
   Wiederherstellung unbrauchbar (Nutzer müssten 2FA neu einrichten).
6. Plattform-Admin anlegen (siehe Ausgabe des Skripts), anmelden, 2FA einrichten.
7. Firewall aktivieren (`/etc/nftables.conf.bike-example` anpassen). Prüfen: `curl https://<host>:8443/health`.

Kunden registrieren sich danach selbst unter `https://<host>/app#/register`.

## Raspberry Pi (je Station, beim Kunden)

1. Im Portal: Station → Einstellungen → **Token erzeugen**. Token und Konfigurationsauszug werden einmal angezeigt.
2. Repo nach `/opt/smart-bike-station`, `sudo deploy/install-pi.sh`.
3. Auszug in `/etc/bike-gateway/config.toml` übernehmen (API-URL, Station-ID, `slot_map`, ggf. `ca_file`),
   Token in `/etc/bike-gateway/gateway.env` (`BIKE_DEVICE_TOKEN=…`, Rechte 600).
4. `sudo systemctl enable --now bike-gateway`. Im Portal erscheint „Zuletzt gesehen“.
5. Bei Verlust oder Tausch des Pi: Token im Portal sperren und neues erzeugen.

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

Kunden löschen ihre Daten selbst (Portal → Organisation → Löschen). Für die Demo: `sudo -u bikestation deploy/delete-demo-data.sh`.
Messdaten und Ereignisse werden automatisch nach der Tarif-Frist gelöscht (Free 7, Schule 30, Pro 90 Tage), das Audit-Log nach 365 Tagen.

## Lokale Entwicklung

`scripts/dev.sh` startet API, Simulator und Gateway auf einem Rechner (ohne TLS, nur lokal).
