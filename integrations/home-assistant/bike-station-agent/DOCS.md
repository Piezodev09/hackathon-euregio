# Smart Bike Station agent

This add-on turns the Home Assistant machine into the gateway of a bike station: the station's
Arduino is plugged into Home Assistant via USB, and the add-on

- sends the readings to your **Smart Bike Station platform** (dashboard, kiosk display, warnings,
  history) – exactly like the Raspberry Pi gateway, and
- publishes the station to **Home Assistant via MQTT discovery**, so every space becomes an entity
  you can use in dashboards and automations – even when the platform is not reachable.

Occupancy comes straight from the Arduino. Movement warnings are decided by the platform and
mirrored into Home Assistant. A warning is a *suspicion*, never proof of theft – please check on site.

## Before you start

1. A running Smart Bike Station platform (self-hosted on Proxmox/Docker or a hosted one) and an
   organisation with a station.
2. For Home Assistant entities: an MQTT broker, usually the official **Mosquitto broker** add-on,
   and the **MQTT** integration. The add-on picks up the broker credentials automatically.
3. The station's Arduino connected to the Home Assistant machine via USB (or use the simulator).

## Installation

1. *Settings → Add-ons → Add-on store → ⋮ → Repositories*, add
   `https://github.com/piezodev09/hackathon-euregio` and close the dialog.
2. Open **Smart Bike Station agent** and click *Install*. Home Assistant builds the add-on on your
   machine; on a Raspberry Pi this takes a few minutes.
3. In the portal of your platform: *Station → Settings → Set up gateway*. Copy the **pairing code**
   (valid for a short time, usable once) and, for a self-hosted platform, the **CA fingerprint**.
4. In the add-on's *Configuration* tab enter:

   | Option | Example | Meaning |
   |---|---|---|
   | `platform_url` | `https://192.168.1.50` | address of your platform (must be `https://`) |
   | `pairing_code` | `7GA34-JR76D` | one-time code from the portal |
   | `ca_fingerprint` | `SHA-256 81:7F:D8:…` | only for a self-hosted platform with its own CA |
   | `source` | `serial` | `serial` = Arduino via USB, `simulator` = simulated demo data |
   | `serial_port` | `auto` | `auto` finds the Arduino; or e.g. `/dev/serial/by-id/usb-Arduino…` |
   | `device_name` | `Home Assistant` | name of this gateway in the portal |
   | `mqtt` | `true` | create Home Assistant entities via MQTT discovery |

5. *Start* the add-on and open the *Log* tab. You should see
   `Paired with station '…'` and `MQTT connected`. After about a minute the gateway shows up in the
   portal as *online*.

The pairing code is only used on the first start (the token is stored in the add-on's private
`/data`). To move the add-on to another station, create a new code in the portal, enter it and
restart the add-on.

## Entities in Home Assistant

All entities belong to one device named after the station:

| Entity | Type | States |
|---|---|---|
| `Space A1` … | binary sensor, *occupancy* | on = occupied, off = free, **unknown** when the sensor has no valid reading |
| `Space A1 unusual movement` … | binary sensor, *problem* | on while the platform has an open movement warning for the space |
| `Free spaces` | sensor | number of free spaces with a valid reading |
| `Arduino connected` | binary sensor, *connectivity* (diagnostic) | USB connection to the Arduino |
| `Gateway CPU temperature` | sensor (diagnostic) | °C, when the host reports it |

If the add-on stops, all entities become *unavailable* (MQTT last will). A space is never reported
as free without a valid reading.

### Example automation

```yaml
alias: Bike station – unusual movement
triggers:
  - trigger: state
    entity_id: binary_sensor.schoolyard_space_a1_unusual_movement
    to: "on"
actions:
  - action: notify.notify
    data:
      message: "Unusual movement at space A1 – please check on site (suspicion, not proof)."
```

Entity IDs depend on your station and space names – pick them in the automation editor.

## Troubleshooting

| Log message | What to do |
|---|---|
| `Not paired yet: set 'platform_url' and 'pairing_code'` | fill in both options and restart |
| `Pairing failed (400: invalid_or_expired_code)` | the code expired or was used – create a new one in the portal |
| `CA fingerprint mismatch` | wrong platform address or fingerprint – copy it again from the portal |
| `Platform not reachable` | check `platform_url` from the Home Assistant machine (same network, firewall) |
| `No MQTT broker found` | install and start the Mosquitto broker add-on, then restart this add-on |
| `Serial connection missing (…) - retrying in 3 s` | check the USB cable and `serial_port` (*Settings → System → Hardware → All hardware* lists the ports) |

Readings are buffered while the platform is offline and sent later; Home Assistant keeps
receiving live occupancy in the meantime.

## Privacy and security

- The add-on sends only space states, movement features and gateway health – no images, no
  personal data.
- Connections to the platform are TLS-encrypted; with `ca_fingerprint` the platform certificate is
  pinned. The device token is stored with mode 0600 in the add-on's `/data`.
- Self-update is disabled inside Home Assistant; updates come as new add-on versions.
