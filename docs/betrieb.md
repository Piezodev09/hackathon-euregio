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

### Zertifikat (selbst signiert, mit IP-Adresse)

`deploy/install-vm.sh` erzeugt mit `deploy/make-cert.sh` ein Zertifikat für Hostname und alle IPs der VM.
Eigene Adresse ergänzen oder erneuern:

```bash
sudo /opt/smart-bike-station/deploy/make-cert.sh --force 192.168.0.114
sudo systemctl restart bike-api
```

Das Portal zeigt beim Einrichten eines Gateways Pin und Fingerabdruck; der Pi vertraut genau diesem Zertifikat.

## Raspberry Pi (je Station, beim Kunden)

Empfohlen: **Agent-Installation per Kopplungscode** – Portal → Station → Einstellungen → *Gateway
einrichten*, die drei angezeigten Befehle auf dem Pi ausführen. Details, Optionen und Fehlersuche:
[agent.md](agent.md).

Alternative ohne Portal-Kopplung (manuell): Token unter *Erweitert: Token manuell erzeugen*,
dann `deploy/install-pi.sh` und `/etc/bike-gateway/config.toml` wie früher (keine Fernverwaltung).

## Start / Stopp / Neustart

| Aktion | VM | Pi |
|---|---|---|
| Status | `systemctl status bike-api` | `systemctl status bike-agent` · `bike-agent status` |
| Logs | `journalctl -u bike-api -f` | `journalctl -u bike-agent -f` |
| Neustart | `systemctl restart bike-api` | Portal → Gateways → *Neu starten* oder `systemctl restart bike-agent` |

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
