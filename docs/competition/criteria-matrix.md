# Criteria matrix – every challenge requirement with its evidence

Each row links a requirement of the project plan to where a juror can check it: the **file**, the
automated **test**, a **screenshot** (in [`screenshots/`](screenshots/)) and the **step** of the
[7-minute demo](demo-script.md). Test names refer to `server/tests/` (S) and `agent/tests/` (A);
run everything with `cd server && python3 -m pytest -q` and `cd agent && python3 -m pytest -q`.

Status: ✅ implemented and tested automatically · 🟡 implemented, still to be confirmed on the real
hardware / on site (see [test-report.md](../test-report.md)) · honest gaps are listed at the end.

| # | Requirement | Evidence (file) | Test | Screenshot | Demo |
|---|---|---|---|---|---|
| 1 | **Sensors per space**: presence + vibration, debounced, fault → "unknown" | `firmware/smart_bike_station/smart_bike_station.ino` (2 s debounce, `presence=-1` after 5 invalid readings), [firmware/README.md](../../firmware/README.md) (BOM, wiring diagram, power budget – *to be verified on site*) | `firmware/check.sh` (4 variants, `-Werror`, run in simavr → output parsed by the agent); A `test_parse_valid`, `test_parse_sensor_error_is_never_free` | – | 1, 3 |
| 2 | **Arduino, Raspberry Pi, Proxmox – each with a clear job** | Arduino: sensors/LEDs/serial · Pi: `agent/bikeagent/` (validate, buffer, heartbeat, updates) · Proxmox: `deploy/proxmox/create-lxc.sh` + `deploy/install-server.sh` (whole platform in one LXC); [architecture.md](../architecture.md) | A `test_gateway_watchdog_reports_errors_when_arduino_silent`, `test_uplink_buffers_while_offline_and_replays`; `bash deploy/proxmox/selftest.sh` | 02 | 1, 4, 5 |
| 3 | **Real time**: visible change ≤ 5 s after a stable sensor change | 2 s debounce + immediate upload + 2 s polling (`ui_poll_interval_s`), kiosk `web/static/js/display.js`; latency to be measured on site (plan 5.3) | S `test_T01_T02_T03_free_occupied_recommendation` | 02, 04, 05 | 1 🟡 |
| 4 | **Recommendation of a free space** (transparent rule, no AI) | `server/app/service.py` (first free space by position, only with valid data) | S `test_T01_T02_T03_free_occupied_recommendation` | 02, 04 | 1 |
| 5 | **Suspicion, never proof** – no accusation of persons | wording in portal/kiosk/webhooks (`event_text` in `server/app/webhooks.py`), grace period after parking, cooldown; no cameras, no names | S `test_T05_T06_alert_after_grace_but_not_while_parking`, `test_single_bump_no_alert_and_old_buffered_data_ignored`, `test_cooldown_ack_and_roles` | 04, 06 | 2 |
| 6 | **"Unknown" is never shown as "free"** | enforced in 4 places: Arduino, agent watchdog (15 s), platform (`stale_after_s` 30 s), browser (connection lost); MQTT sends *unknown* too | S `test_no_data_is_unknown_never_free`, `test_T07_sensor_error_and_stale_are_unknown`; A `test_parse_sensor_error_is_never_free`, `test_measurements_update_states_and_free_count` | 04 (legend) | 3 |
| 7 | **AI with a baseline and an honest comparison** | `server/app/anomaly.py` (rule + Isolation Forest on the same features), `ml/train.py` (split by complete runs, report), [ai-factsheet.md](../ai-factsheet.md): simulated data – rule 9/9 detected · 4/45 false alarms, AI 9/9 · 5/45 → **rule stays visible** | S `test_ml_model_loaded_and_runs_in_shadow` | 02 (AI card) | 6 |
| 8 | **Backend and database on Proxmox** | FastAPI + SQLite (WAL, schema v4 with migrations) in an unprivileged Debian 12 LXC; HTTPS with its own CA, daily backups, `pct snapshot`; alternative: Docker (`deploy/docker/`) | S `test_migration_from_schema_2`, `test_migration_from_schema_3_keeps_events`, `test_production_allows_ip_url_without_mail`; E2E in a container (see test report) | – | 5 |
| 9 | **Occupancy history / heatmap** | last 24 h per space + *typical week* (weekday × hour, time-weighted, local time zone), text summary for screen readers; simulated data labelled | S `test_T13_summary_time_weighted_and_labels_simulated`, `test_weekly_pattern_is_tenant_isolated`, `test_demo_creates_labelled_history_and_landing_demo` | 02, 03 | 1 |
| 10 | **Security** | scrypt, TOTP 2FA + recovery codes, lock-out, rate limits, server sessions, CSRF + origin check, strict CSP, tenant isolation, hashed device tokens, pinned CA for agents, signed + SSRF-checked webhooks, read-only API keys, audit log, secure-by-default production checks; [security-privacy.md](../security-privacy.md) | S `test_auth.py` (16), `test_tenancy.py` (12), `test_security_headers`, `test_csrf_required_for_cookie_requests`, `test_ssrf_address_rules`, `test_api_key_reads_only_its_tenant`, `test_T09_device_write_without_or_with_bad_token_rejected_and_logged`, `test_ca_is_served_embedded_and_pinned`; A `test_safe_extract_rejects_path_tricks`, `test_update_verifies_hash_and_switches` | 09 | 7 |
| 11 | **Privacy** | no cameras/RFID/names; data minimisation (measurements 7/30/90 days by plan, leads 180 days, audit 365 days), data export (Art. 20), deletion by cascade, no tracking, self-hosting keeps data on site; webhook payloads without personal data | S `test_export_contains_no_secrets`, `test_delete_org_cascades`, `test_lead_rate_limit_and_retention`, `test_export_contains_integrations_without_secrets` | 04 (kiosk notice) | 7 |
| 12 | **Sustainability** | reuse of existing Proxmox host, ~4 W per station (**estimate**, to be measured with a USB power meter: [firmware/README.md](../../firmware/README.md#energy-plan-11)), dimmable LEDs (`LED_BRIGHTNESS`), long-lived standard parts, local operation without cloud, retention limits | – | 01 (landing) | 5 🟡 |
| 13 | **Accessibility** (WCAG 2.2 AA) | symbols + text, never colour alone; keyboard operation, visible focus, skip link, AA contrast in light and dark mode, focusable scroll regions, text summary of the heatmap; `scripts/check-a11y.mjs` (axe-core on 26 page states – public pages light/dark, portal desktop/phone: 0 serious/critical findings, no horizontal scrolling at 390 px) | `node scripts/check-a11y.mjs` (Playwright + axe-core) | 04, 05 | 7 |
| 14 | **Multilingual** EN / DE / NL | portal + landing `web/static/js/i18n.js`, kiosk `display-i18n.js`, Home Assistant add-on `translations/`; checked by `scripts/check-i18n.mjs`; DE/NL still to be reviewed by native speakers | `node scripts/check-i18n.mjs` | 08 (DE), 04 | 7 |
| 15 | **Submission**: code, documentation, tests, demo, pitch | [README.md](../../README.md) (code map), [CONTRIBUTING.md](../../CONTRIBUTING.md), `docs/`, 116 server + 62 agent tests, [demo-script.md](demo-script.md), [pitch-outline.md](pitch-outline.md), [offline-kit.md](offline-kit.md), [jury-kurzfassung.md](jury-kurzfassung.md) | full test run | all | – |

## Beyond the requirements

| Topic | Evidence | Test | Screenshot | Demo |
|---|---|---|---|---|
| Multi-tenant SaaS with roles and plans | `server/app/routes/org.py`, `plans.py` | S `test_roles`, `test_plan_limits_and_upgrade`, `test_tenants_cannot_see_each_other` | 09 | – |
| Gateway fleet: pairing in 60 s, remote commands, self-update with rollback | [agent.md](../agent.md) | S `test_enrollment_code_flow`, A `test_automatic_rollback_after_failed_starts` | – | 4 |
| Self-hosting without domain and mail server | `deploy/install-server.sh`, setup link, invitation/reset links with QR | S `test_setup_token_is_single_use`, `test_invitation_link_and_qr_without_mail`, `test_reset_links_respect_roles` | – | 5 |
| Integrations: webhooks (Teams/Slack/Discord/HA), REST API, MQTT/Home Assistant, Docker | [api.md](../api.md#integrations-integrations), `integrations/home-assistant/` | S `test_integrations.py` (16), A `test_mqtt.py` (7), `test_homeassistant.py` (14) | 07 | 2, 4 |
| Gateway outage alarm (exactly one per outage, closes itself) | `check_gateways` in `server/app/service.py` | S `test_gateway_offline_raises_one_event_and_online_info` | 06 | 3 |
| Landing page with live demo and lead form | `web/index.html` | S `test_landing.py` (13) | 01 | – |

## Honest gaps (open until the event)

- The Arduino sketch compiles without warnings for the Uno and runs in the simavr simulator
  (`firmware/check.sh`), but has not run on the final hardware yet; pins and voltages are
  placeholders → *verify on site*. Digital presence sensors cannot report a broken wire – ultrasonic is the default.
- The AI was trained on **simulated** data only; real recordings (plan 8.2) are still to be made.
- The latency target (≤ 5 s) and the energy figure are design values until measured with the real station.
- `create-lxc.sh` was tested against fake Proxmox commands and `--dry-run`, not on a real Proxmox host;
  the Home Assistant add-on was tested as a container with a fake Supervisor API, not inside Home Assistant.
- German and Dutch texts have not been reviewed by native speakers.
- No external penetration test; agent releases are checksummed but not yet signed.
