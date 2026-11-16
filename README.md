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

1. The **bracelet** sends a 26-byte packet over Bluetooth every second.
   Bluetooth only reaches the laptop or phone that has the website open.
2. The **website** (`frontend/bracelet.js`) decodes the packet and sends the
   reading over the internet to the backend (`POST /api/sensors`), with all its
   fields: heart rate, counter, device, test flag, error flag and battery.
3. The **backend** (`backend/app.py`) stores the reading in PostgreSQL, table
   `readings` (`backend/database.py`). Bad readings are stored too.
4. The **analytics** (`analytics/src/heart_rate_summary.py`) checks the stored
   readings of the bracelet that sent the latest reading and leaves out:

   | Reason | Rule |
   |---|---|
   | sensor error | the bracelet's error flag is not 0 |
   | sent twice | same counter and same bracelet uptime as an earlier reading |
   | impossible value | below 30 or above 220 BPM |
   | sensor stuck | exactly the same value 30 times in a row or more |

   It also counts readings that never arrived (gaps in the counter).
5. The results are saved **per minute** in PostgreSQL, table `heart_rate_minute`.
6. The **website** (Insights page) shows the summary and a chart of the last 15
   minutes. The **AI advisor** only gets the readings that passed the checks.

Only the backend talks to the database. The website and the bracelet never do,
so the database password stays on the server.

## The tables

| Table | One row is | Columns |
|---|---|---|
| `readings` | one reading, exactly as it arrived | type, value, ts / received_at, device_id, sequence, uptime_ms, is_test, flags, battery, stored_at |
| `heart_rate_minute` | one minute of one bracelet | device_id, minute / minute_start, average, min, max, used, removed |

The tables are created automatically when the backend starts.

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
   It prints `Connected to the PostgreSQL database.`

## Show that it works

1. Start the backend.
2. In a second terminal, in the project folder, send test readings:
   `python analytics/src/fake_bracelet.py --fast --problems`
3. Show what is stored: `python backend/show_database.py`
4. Open http://localhost:5000 and go to **Insights**: it shows
   "55 used, 3 left out (1 sent twice, 1 impossible value, 1 sensor error),
   3 never arrived" and the chart.
5. Stop the backend (Ctrl+C), start it again, refresh the page: the data is still there.
6. In pgAdmin: Databases > moodpulse > Schemas > public > Tables > readings,
   right-click > View/Edit Data > All Rows.

With the real bracelet: on the **Face preview** page click Connect bracelet,
choose "XIAO BLE", tick "Allow simulated bracelet readings" (the firmware is in
simulation mode) and click Start readings.

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
| `POST /reset-session` | clears the camera history and deletes all readings and results |
| `GET /health` | shows `"database": true` when PostgreSQL is reachable |

If PostgreSQL is not reachable, the backend still starts; the bracelet routes
answer with a clear error, and connect again once PostgreSQL is running.

## Tests

`python -m pytest` in the project folder. 21 tests check the analytics.
15 more test the backend with PostgreSQL; they use `TEST_DATABASE_URL` from
`backend/.env` and **empty that database**, so it must not be the real one.
Without it they are skipped.

## Known limits

- No user accounts yet: readings belong to a bracelet, not to a person. The
  summary uses the bracelet that sent the latest reading.
- The time of a reading is when the website received it; the bracelet has no clock.
- The analytics runs when the summary is requested (the Insights page does this
  every 5 seconds), not by itself in the background.
- Camera results and journal entries are not in the database yet.
