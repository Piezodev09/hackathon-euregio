# Test report

No test passes just because it worked once by chance. Repeat T04–T06 several times. Run T08/T14
only in a controlled way and after agreement with the IT department.

## Automated tests (without hardware)

```bash
cd server && python3 -m pytest -q     # 116 passed
cd agent && python3 -m pytest -q      # 62 passed
node scripts/check-i18n.mjs           # i18n OK (EN/DE/NL, same keys)
scripts/sync-ha-addon.sh --check      # Home Assistant add-on in sync with agent/
firmware/check.sh                     # sketch builds in 4 variants (-Werror) + runs in simavr
bash deploy/proxmox/selftest.sh       # create-lxc.sh against fake Proxmox commands
shellcheck scripts/*.sh deploy/*.sh deploy/proxmox/*.sh deploy/docker/*.sh agent/*.sh firmware/*.sh integrations/home-assistant/*/run.sh
node scripts/check-a11y.mjs           # axe-core, 26 page states, needs scripts/dev.sh running
```

Mapping of the plan's acceptance tests to automated tests (`server/tests/…` unless noted):

| Test | Automated in | Status |
|---|---|---|
| T01 empty space / T02 occupied / T03 recommendation | `test_api.py::test_T01_T02_T03_free_occupied_recommendation` | passed |
| never "free" without data | `test_api.py::test_no_data_is_unknown_never_free`; agent `test_parse_sensor_error_is_never_free` | passed |
| T05 warning / T06 normal parking (grace period) | `test_api.py::test_T05_T06_alert_after_grace_but_not_while_parking`, `test_single_bump_no_alert_and_old_buffered_data_ignored` | passed |
| T07 sensor failure | `test_api.py::test_T07_sensor_error_and_stale_are_unknown`; agent `test_gateway_watchdog_reports_errors_when_arduino_silent`; `firmware/check.sh` (real firmware without sensors → error) | passed |
| T08 network interruption | `test_T07_sensor_error_and_stale_are_unknown` (stale), agent `test_uplink_buffers_while_offline_and_replays`, `test_uplink_buffer_is_bounded`; gateway outage alarm `test_integrations.py::test_gateway_offline_raises_one_event_and_online_info` | passed |
| T09 API protection | `test_api.py::test_T09_device_write_without_or_with_bad_token_rejected_and_logged`, `test_auth.py::test_csrf_required_for_cookie_requests` | passed |
| T10 read-only access | `test_tenancy.py::test_roles`, `test_integrations.py::test_api_keys_need_admin_and_valid_key` | passed |
| T11 languages | `scripts/check-i18n.mjs` (keys complete); texts not yet reviewed by native speakers | passed (keys) |
| T12 keyboard / without colour | `scripts/check-a11y.mjs` (axe-core WCAG 2.2 AA, 0 serious findings); symbols + text in every state | passed (automated part) |
| T13 history | `test_api.py::test_T13_summary_time_weighted_and_labels_simulated`, `test_landing.py::test_weekly_pattern_is_tenant_isolated` | passed |
| Duplicates | `test_api.py::test_duplicate_sequence_is_ignored`, `test_batch_endpoint` | passed |

### End-to-end checks carried out (without real hardware)

| Check | How | Result |
|---|---|---|
| Firmware ↔ agent protocol | sketch compiled with `avr-g++` for ATmega328P (4 sensor variants, no warnings), executed in **simavr**, serial output parsed by the agent | hello line + `presence=-1/state=error` for A, B, C with increasing `seq` → *unknown* |
| Self-hosting installer | `deploy/install-server.sh --no-systemd` in the container on a loopback IP: own CA with IP SANs, HTTPS, one-time setup link, first admin via the setup API (invitation/reset links without mail: automated tests in `test_selfhost.py`) | passed |
| Pi pairing with pinned CA | exactly the portal commands (`curl -k`, `sha256sum -c`, `agent.sh --code … --no-systemd --source simulator`) against the HTTPS platform | paired, heartbeats, `0600` state |
| Platform Docker image | built, started with a volume; setup via API, 2FA, station, pairing code via `bike-station` CLI; restart on the same volume keeps data and CA | passed |
| Docker Compose | `compose.yaml` up, `bike-station demo --reset` inside, demo agent paired over `https://platform:8443` with the fingerprint | passed |
| Webhooks | receiver container in the same Docker network: test button, `gateway_offline` after stopping the agent (≈ 3 min), `gateway_online` on restart; signatures verified with the secret, tampered body rejected | passed |
| MQTT / Home Assistant discovery | agent against a real Mosquitto broker: 11 discovery configs, states, availability online/offline (last will) | passed |
| Home Assistant add-on | image built like the Supervisor does (`BUILD_FROM`), run with `options.json`, fake Supervisor API and Mosquitto: pairing with a pinned CA (`SHA-256 …` label accepted), MQTT credentials from the Supervisor, discovery, restart without re-pairing | passed |
| Portal and landing page | Playwright: sign-in, integrations (key shown once, rejected webhook target, test delivery), pairing dialog, languages, 390 px phone; axe-core light/dark | 0 serious findings, no console/CSP errors, no horizontal scrolling |
| AI comparison | `ml/generate_synthetic.py` (seed 42) + `ml/train.py` | reproduces [ai-factsheet.md](ai-factsheet.md): rule 9/9 · 4/45, AI 9/9 · 5/45 |

In addition end-to-end with the simulator (simulator → agent → platform → browser): occupancy,
recommendation, warning when shaking (rule + AI shadow), sensor fault → "unknown", simulator stopped
→ after 30 s all spaces "unknown – data stale", acknowledging in the browser, languages EN/DE/NL,
narrow view (390 px). All with **simulated** data.

### Agent installation (end-to-end, without a real Pi)

Pairing code created in the portal → exactly the displayed commands executed (download,
`sha256sum -c` → OK, `agent.sh --code … --no-systemd --source simulator`) → device paired, state file
`0600`, the same code rejected a second time → agent started, *online* in the portal with health
data → space D added in the portal → agent takes over configuration 2 without a restart → *Renew
token* → rotation without interruption → *Restart* → agent exits with code 3 (systemd restarts it) →
platform with agent version 1.0.1 → agent downloads the update, verifies SHA-256, switches, restarts,
reports 1.0.1 and confirms the update. **Open:** test on a real Raspberry Pi with systemd and Arduino.

### Not testable in this environment (honest)

- a real **Proxmox** host: `create-lxc.sh` only via `--dry-run` and the fake-command self-test;
- the **Home Assistant Supervisor** itself (add-on store, build, `services: mqtt:want`) – emulated;
- a real **Raspberry Pi with Arduino and sensors**: pins, voltages, calibration, latency, energy;
- phone push via Teams or the Home Assistant cloud (needs internet and accounts).

## Acceptance tests with hardware (plan chapter 14)

| ID | Procedure | Passed if … | Date | Version | Rep. | Observation | Result | By |
|---|---|---|---|---|---|---|---|---|
| T01 | remove the demo object | space free after stabilisation | | | | | open | |
| T02 | park the object | LED/text and dashboard occupied | | | | | open | |
| T03 | at least one space free | an actually free space is recommended | | | | | open | |
| T04 | park/remove several times | no permanent flickering | | | | | open | |
| T05 | controlled shock | warning with space/time; time until shown: ___ s | | | | | open | |
| T06 | park/remove normally | false alarms counted: ___ of ___ | | | | | open | |
| T07 | disconnect the sensor | unknown/error, never a wrong "free" | | | | | open | |
| T08 | disconnect Pi and platform | error visible; synchronised afterwards | | | | | open | |
| T09 | write without a token | rejected and logged | | | | | open | |
| T10 | view without admin rights | no admin action possible | | | | | open | |
| T11 | EN/DE/NL | core states and warning translated | | | | | open | |
| T12 | keyboard / without colour (greyscale) | understandable and operable | | | | | open | |
| T13 | several measurements | chart correct, data source labelled | | | | | open | |
| T14 | restart Pi/platform | services start, state re-captured | | | | | open | |
| T15 | same cases rule vs. AI | results and limits shown (`ml/report.md`) | | | | | open | |

Measure latency (plan 5.3): note the time of the stable sensor change and of the visible dashboard
change. Target ≤ 5 s (2 s debounce + transfer + 2 s polling).

## Known limitations / defects

- Arduino sketch compiled and run in a simulator, not yet on the real hardware (pins, sensors, voltages).
- Digital presence sensors cannot detect a broken wire; use ultrasonic or an active-high wiring
  ([firmware/README.md](../firmware/README.md#sensor-selection-in-the-sketch)).
- AI model trained on simulated data only so far.
- DE/NL texts not yet reviewed by a native speaker.
