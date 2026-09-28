# Test report

No test passes just because it worked once by chance. Repeat T04–T06 several times. Run T08/T14
only in a controlled way and after agreement with the IT department.

## Automated tests (without hardware)

```bash
cd server && python3 -m pytest -q
cd agent && python3 -m pytest -q
```

| Test | Automated in | Status |
|---|---|---|
| T01 empty space / T02 occupied | `server/tests/test_api.py::test_T01_T02_free_and_occupied` | passed |
| T03 recommendation | `test_T03_recommendation_is_first_free_by_position` | passed |
| T05 warning | `test_T05_unusual_movement_creates_alert_after_grace` | passed |
| T06 normal parking (grace period) | `test_T06_parking_within_grace_period_no_alert`, `test_single_bump_no_alert` | passed |
| T07 sensor failure | `test_T07_sensor_error_is_unknown`, agent watchdog test | passed |
| T08 network interruption | `test_stale_data_becomes_unknown`, agent buffer tests, `test_old_buffered_data_does_not_trigger_alert` | passed |
| T09 API protection | `test_T09_write_without_token_rejected_and_logged` | passed |
| T10 read-only access | `test_T10_read_view_cannot_do_admin` | passed |
| T13 history | `test_T13_summary_time_weighted_and_labels_simulated` | passed |
| Duplicates | `test_duplicate_sequence_is_ignored`, `test_batch_endpoint` | passed |

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

- Arduino sketch not yet compiled/tested on real hardware.
- AI model trained on simulated data only so far.
- DE/NL texts not yet reviewed by a native speaker.
