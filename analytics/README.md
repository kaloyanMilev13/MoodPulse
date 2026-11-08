# Analytics module

Owner: **Dobromir** 

The analytics module processes the data stored in the cloud: it checks the
quality of received readings, calculates personal baselines and trends,
and monitors whether each bracelet is sending data (requirements ANA-01 to
ANA-11 in the project document).

## Folder structure

```
analytics/
├── src/
│   └── generate_sample_data.py   simulated data for the whole team
├── tests/
│   └── test_generate_sample_data.py
├── requirements.txt
└── README.md
```

## Setup

```bash
cd analytics
pip install -r requirements.txt
```

## Simulated data

Until the bracelet and website work, everyone uses simulated data
Generate it with:

```bash
python src/generate_sample_data.py
```

This writes four files to `analytics/sample_data/`:

| File | Contents |
|---|---|
| `users.json` | Fake users, each with one bracelet (`device_id`) |
| `readings.json` | Heart rate, skin conductance and movement readings, in the order the server **received** them |
| `journal.json` | Check-ins with a mood and an optional text |
| `answer_key.json` | Everything deliberately hidden in the data, plus each user's true baseline, for testing the analysis |

Options:

| Option | Default | Meaning |
|---|---|---|
| `--users` | 3 | Number of fake users |
| `--days` | 7 | Number of days (minimum 3) |
| `--interval` | 300 | Seconds between readings (300 = every 5 minutes) |
| `--seed` | 42 | Same seed = exactly the same data |
| `--start` | a week ago | First day, `YYYY-MM-DD` |
| `--out` | `sample_data` | Output folder |

### What is hidden in the data

| Event | What it looks like | Tests requirement |
|---|---|---|
| `stress` | 13:00–17:00 on some days: higher heart rate and skin conductance, little movement. A stressed/anxious journal entry that evening. | ANA-02, ANA-09 |
| `exercise` | 18:00–19:00 on some days: high movement and heart rate. This is **not** stress. | ANA-08 |
| `not_worn` | 07:30–08:00 every day: heart rate `unavailable`, skin conductance near 0, no movement. | ANA-01 |
| `flatline` | Skin conductance stuck at one exact value for 2 hours. | ANA-01 |
| `impossible_value` | Single heart-rate readings of 230 or 25 BPM. | ANA-01 |
| `duplicate` | The same measurement received twice. | ANA-01 |
| `unavailable_reading` | Random readings the firmware marked as failed. | ANA-01 |
| `connection_gap` | 20:00–23:00 on one day: readings buffered and received after the gap. | ANA-07 |
| `no_data` | 01:00–03:00 on one day: battery empty, no readings at all. | ANA-10 |

Times above are Amsterdam local time; all timestamps in the files are UTC.

## Proposed data format

To be agreed with Kaloyan (firmware), Radoslav (database) and Jonathan (website).
JSON, UTF-8, ISO 8601 timestamps in UTC,
heart rate as integer BPM, skin conductance in µS, server-generated UUIDs.

**Reading**

```json
{
  "reading_id": "c920aae9-9328-4178-81bc-32735b5eed09",
  "device_id": "07a0ca6e-0822-48f3-ac03-1199972a8469",
  "type": "heart_rate",
  "value": 72,
  "unit": "BPM",
  "status": "valid",
  "measured_at": "2026-11-05T09:15:00Z",
  "received_at": "2026-11-05T09:15:02Z"
}
```

- `type`: `heart_rate` (BPM, integer), `skin_conductance` (µS), `movement` (g)
- `status`: `valid` or `unavailable` (marked by the firmware, `value` is then `null`)
- `measured_at`: when the bracelet measured it. `received_at`: when the server got it.
  Both are needed to handle readings that were buffered and uploaded late.

**Journal entry**

```json
{
  "entry_id": "a8ffb81c-b98a-4e5f-acbf-31fb177be9e6",
  "user_id": "8fadc1a6-06cb-4fb3-9a1d-e644815ef6d1",
  "mood": "stressed",
  "text": "Deadline tomorrow, feeling tense.",
  "created_at": "2026-11-05T19:42:00Z"
}
```

- `mood`: one of `happy`, `calm`, `neutral`, `tired`, `stressed`, `anxious`, `sad`
- `text`: optional (`null` if empty)

**Open questions**

- Movement: raw accelerometer values or an activity/step count? (Kaloyan)
- Sampling interval of the real bracelet? (Kaloyan)
- Final mood categories? (Jonathan)

## Tests

```bash
python -m pytest
```
