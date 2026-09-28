# AI fact sheet: detection of unusual movement

**Question:** does the movement pattern at an occupied space deviate noticeably from normal parking
and leaving?

**Meaning of an anomaly:** only "this pattern was unusual compared with the measurements so far".
Not proof of theft, no statement about people.

## Features (per evaluation, window 10 s)

| Feature | Description |
|---|---|
| `n_peaks` | messages with `vibration_score ≥ peak_threshold` |
| `n_active` | messages with `vibration_score > 0` |
| `max_score` | strongest peak |
| `mean_active_score` | mean strength of the active messages |
| `since_change_s` | seconds since the last occupancy change (max. 600) |

Every message with vibration at an occupied space is evaluated. Training (`ml/`) and operation
(`server/app/anomaly.py`) use **the same** function `features_at`.

## Methods

| | Baseline rule | AI |
|---|---|---|
| Logic | ≥ `min_peaks` (3) peaks ≥ `peak_threshold` (300) within 10 s | Isolation Forest (scikit-learn, 200 trees) |
| Training | – | normal runs only, after the grace period |
| Grace period | no warning within 15 s after an occupancy change | same |
| Where | platform | platform |
| Failure | – | model missing/broken → "not available", occupancy keeps working |

Both always run in parallel. Which one produces the **visible** warning is chosen per station in
the portal (*Settings → Visible warnings from*); the other one is stored as a `shadow` event and
can be shown under *Events → Show comparison results*.

## Recording data (plan 8.2)

1. Perform normal runs several times: empty, parking, parked, light bump, neighbouring space, leaving.
2. Simulate anomalous runs in a controlled way: repeated strong movement of the demo object.
   Never instruct anybody to actually steal or damage anything.
3. Note start/end of every run and export it:
   `python3 ml/export_windows.py --db … --station st_… --slot A --since … --until … --label normal --run-id r01`
4. Train and compare: `python3 ml/train.py ml/data/recorded.csv`
   → split by complete runs, result in `ml/report.md`, model in `ml/models/`.
5. Restart the platform so the model is loaded (station view → *Movement detection*: "AI model available").

## Result

| Data | Runs (test) | Rule: detected / false alarms | AI: detected / false alarms | Decision |
|---|---|---|---|---|
| Simulated (pipeline check, `python3 ml/generate_synthetic.py`, seed 42) | 54 (9 anomalous, 45 normal) | 9/9 · 4/45 | 9/9 · 5/45 | rule stays visible |
| Real recordings | | | | |

The simulated figures only show that the pipeline works – not that the method is effective.

**Go/no-go:** if the AI causes more problems than the rule, the visible warnings stay with the rule;
the AI is explained as a demonstrated but not yet sufficiently reliable research feature.

## Known limitations

- A few hackathon recordings are not enough for reliable statements about real thefts.
- A new bike, people passing by, wind, neighbouring spaces or leaving can cause false alarms.
- A purely digital vibration sensor only delivers pulse counts – the strength features are coarse then.
