# Analytics and database

Owner: **Dobromir** (Smart Industry)

The bracelet sends one heart-rate reading per second. This part of MoodPulse
stores those readings in a **PostgreSQL** database, checks which ones are
usable, and saves the results so the website and the AI advisor can use them.

## How the data travels

```
Bracelet --Bluetooth--> Website in the browser --internet--> Backend (Flask) --> PostgreSQL
                        (frontend/bracelet.js)               + analytics
```

1. The **bracelet** sends a 25-byte packet over Bluetooth every second
   (format below). Bluetooth only reaches the laptop or phone that has the
   website open. If Bluetooth drops, the bracelet keeps readings in a buffer
   and sends them all at once when it reconnects (flag 0x20 on those).
2. The **website** (`frontend/bracelet.js`) decodes the packets and sends them
   to the backend in batches (`POST /api/sensors`), with heart rate, counter,
   uptime, device, flags, battery and `buffered`. For buffered readings it
   already estimates the measured time from the uptime. Heart rates outside
   30-220 BPM are skipped (the backend refuses them); readings with an error
   flag are sent with a warning, so the analytics can count them.
3. The **backend** (`backend/app.py`) stores the readings with
   `backend/database.py`: in PostgreSQL, table `readings`, when `DATABASE_URL`
   is set, otherwise in memory (lost when the server stops). Bad readings are
   stored too.
4. The **analytics** (`analytics/src/heart_rate_summary.py`) checks the stored
   readings of the bracelet that sent the latest reading and leaves out:

   | Reason | Rule |
   |---|---|
   | sensor error | flag 0x02 (SENSOR_ERROR) is set |
   | poor contact | flag 0x10 (POOR_WRIST_CONTACT) is set |
   | Bluetooth error | flag 0x01 (BLE_ERROR) is set |
   | sent twice | same counter and same bracelet uptime as an earlier reading |
   | impossible value | below 30 or above 220 BPM |
   | sensor stuck | exactly the same value 30 times in a row or more |

   It also counts readings that never arrived (gaps in the counter), and
   **recovers from interruptions**: readings that come late from the bracelet's
   buffer are put back at the time they were measured (worked out from the
   bracelet's uptime), so they land in the right minute of the chart. The
   summary says how many arrived late (late by the uptime, or marked
   `buffered` by the website).
5. The results are saved **per minute** in PostgreSQL, table `heart_rate_minute`.
6. The **website** (Insights page, card "Heart rate") shows the numbers, a line
   chart over 1 minute, 15 minutes, 1 hour or 24 hours, a strip with the result of the
   checks, and why readings were left out. The **AI advisor** only gets the readings that passed the checks.

Only the backend talks to the database. The website and the bracelet never do,
so the database password stays on the server.

## The bracelet packet (firmware `firmware/buffering`, 18 Nov)

25 bytes, little-endian, Python format `<HIIBIllBB`:

| Field | Type | Meaning |
|---|---|---|
| device_id | uint16 | which bracelet |
| sequence | uint32 | counter, +1 per reading |
| uptime_ms | uint32 | ms since the bracelet started |
| heart_bpm | uint8 | heart rate |
| heart_lastBeat | uint32 | time of the last beat |
| heart_IrValue, heart_redValue | int32 | raw sensor values |
| batt_lvl | uint8 | battery % |
| flags | uint8 | bitmask, see below |

| Flag | Value | What the analytics does |
|---|---|---|
| BLE_ERROR | 0x01 | reading left out |
| SENSOR_ERROR | 0x02 | reading left out |
| BUFFER_OVERFLOW | 0x04 | nothing (the gap shows up as "never arrived") |
| LOW_BATTERY | 0x08 | nothing (website shows a warning) |
| POOR_WRIST_CONTACT | 0x10 | reading left out |
| OFFLINE_BUFFERING | 0x20 | nothing |
| SIMULATION_MODE | 0x40 | marked as test data |
| BATTERY_CHARGING | 0x80 | nothing |

## The tables

| Table | One row is | Columns |
|---|---|---|
| `readings` | one reading, exactly as it arrived | type, value, ts / received_at, device_id, sequence, uptime_ms, is_test, flags, battery, buffered, stored_at |
| `heart_rate_minute` | one minute of one bracelet | device_id, minute / minute_start, average, min, max, used, removed |

The tables are created automatically when the backend starts (a `readings`
table from an older version gets the `buffered` column added).

## Set up (once per computer)

1. **Install PostgreSQL** from postgresql.org. Remember the password you give
   the `postgres` user. Keep port 5432. pgAdmin is installed with it.
2. **Create the database**: open pgAdmin, open Servers > PostgreSQL, right-click
   Databases > Create > Database, name it `moodpulse`, Save.
   For the tests, create a second one called `moodpulse_test`.
3. **Install the Python libraries** (in the project folder):
   `python -m pip install -r backend/requirements.txt`
4. **Tell the backend where the database is**: copy `backend/.env.example` to
   `backend/.env` and replace `YOUR_PASSWORD` with your PostgreSQL password.
5. **Start the backend**: `cd backend` then `python app.py`.
   It prints `Bracelet readings are stored in: postgresql`.
   `http://localhost:5000/health` shows `"storage": "postgresql", "database": true`.

Without step 4 the backend still runs, but keeps the readings in memory
(`"storage": "memory"`).

## On the VM

The same steps, with PostgreSQL installed on the VM itself (Ubuntu:
`sudo apt install postgresql`, then in `sudo -u postgres psql`:
`CREATE USER moodpulse WITH PASSWORD '...'; CREATE DATABASE moodpulse OWNER moodpulse;`)
and in `backend/.env` on the VM:
`DATABASE_URL=postgresql://moodpulse:...@localhost:5432/moodpulse`
(with Docker: the database container's name instead of `localhost`).
The server shows `backend/Index.html` and `backend/static/` if they exist,
otherwise `frontend/jonathan_index.html` and `frontend/`; so after an update
copy the new `frontend` files over the old ones there.

## Show that it works

1. Start the backend.
2. In a second terminal, in the project folder, send test readings:
   `python analytics/src/fake_bracelet.py --fast --problems`
3. Show what is stored: `python backend/show_database.py`
4. Open http://localhost:5000 and go to **Insights**: it shows
   "55 used, 3 left out (1 sent twice, 1 poor wrist contact, 1 sensor error),
   3 never arrived", a line about readings that arrived late, and the chart.
   Without `--fast` it sends one reading per second and holds 10 readings back
   for 10 seconds, like a real Bluetooth drop.
5. Stop the backend (Ctrl+C), start it again, refresh the page: the data is still there.
6. In pgAdmin: Databases > moodpulse > Schemas > public > Tables > readings,
   right-click > View/Edit Data > All Rows.

With the real bracelet: on the **Face preview** page click Connect bracelet
and choose "XIAO BLE"; readings start by themselves (use Edge or Chrome).

## Change it

- **All database code is in `backend/database.py`.** The tables are described in
  `SCHEMA` at the top; each function below it does one job (store readings,
  read readings, save results, clear).
- **Add a column**: add it to `SCHEMA`, and on databases that already exist run
  for example `ALTER TABLE readings ADD COLUMN note TEXT;` in pgAdmin's Query Tool
  (`CREATE TABLE IF NOT EXISTS` does not change a table that is already there).
  During development you can also delete the tables in pgAdmin; the backend
  makes them again when it starts.
- **Change the checks** (for example the 30-220 BPM limits): the constants at
  the top of `analytics/src/heart_rate_summary.py`.
- **Use another database server** (for example on the VM): only change
  `DATABASE_URL` in `backend/.env`.

## Server routes used by this part

| Route | What it does |
|---|---|
| `POST /api/sensors` | stores readings (only while bracelet readings are started) |
| `GET /api/sensors` | the readings of the last 24 hours (used by the AI advisor) |
| `GET /api/heart-summary` | runs the analytics, stores the per-minute results, returns the summary and the last 60 minutes |
| `GET /api/heart-history?minutes=15` | the data for the heart-rate charts (1, 15, 60 or 1440 minutes, counted back from the latest reading): summary, per-minute results and, up to an hour, every checked reading |
| `POST /reset-session` | clears the camera history and deletes all readings and results |
| `GET /health` | shows `"database": true` when PostgreSQL is reachable |

If PostgreSQL is not reachable, the backend still starts; the bracelet routes
answer with a clear error, and connect again once PostgreSQL is running.

## Tests

`python -m pytest` in the project folder. 37 tests check the analytics.
22 more test the backend with PostgreSQL; they use `TEST_DATABASE_URL` from
`backend/.env` and **empty that database**, so it must not be the real one.
Without it they are skipped.

## Known limits

- No user accounts yet: readings belong to a bracelet, not to a person. The
  summary uses the bracelet that sent the latest reading.
- The bracelet has no clock. The website stamps each reading when it arrives
  (and estimates the time of buffered ones); the analytics checks this again
  using the bracelet's uptime.
- A drop longer than the bracelet's buffer loses readings; they are counted as
  "never arrived".
- The analytics runs when the summary is requested (the Insights page does this
  every 5 seconds), not by itself in the background.
- Camera results and journal entries are not in the database yet.
