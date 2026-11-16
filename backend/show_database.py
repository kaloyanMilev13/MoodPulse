"""
MoodPulse - show what is in the database.

Run it to show your group that the readings really are stored in PostgreSQL:
    python backend/show_database.py

It prints how many readings are stored, the latest ones, and the analytics
results per minute. It only reads; it never changes anything.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402


def main():
    try:
        db = database.connect()
    except Exception as error:
        print(f"Could not connect to PostgreSQL: {error}")
        print("Check that PostgreSQL is running and that DATABASE_URL in backend/.env is right.")
        return

    with db:
        total = db.execute("SELECT COUNT(*) AS n FROM readings").fetchone()["n"]
        print(f"Readings stored: {total}")

        bracelets = db.execute(
            "SELECT device_id, COUNT(*) AS readings,"
            " to_char(MIN(received_at), 'YYYY-MM-DD HH24:MI:SS') AS first,"
            " to_char(MAX(received_at), 'HH24:MI:SS') AS last"
            " FROM readings GROUP BY device_id ORDER BY MAX(ts) DESC"
        ).fetchall()
        for b in bracelets:
            print(f"  bracelet {b['device_id']}: {b['readings']} readings, "
                  f"{b['first']} to {b['last']}")

        print("\nLatest 10 readings (table: readings)")
        print(f"  {'time':<20} {'bracelet':>8} {'counter':>8} {'BPM':>5} {'error':>6} {'test':>5}")
        latest = db.execute(
            "SELECT to_char(received_at, 'YYYY-MM-DD HH24:MI:SS') AS time, device_id,"
            " sequence, value, flags, is_test FROM readings"
            " ORDER BY ts DESC, id DESC LIMIT 10").fetchall()
        for r in reversed(latest):
            print(f"  {r['time']:<20} {str(r['device_id']):>8} "
                  f"{str(r['sequence']):>8} {r['value']:>5.0f} {str(r['flags']):>6} "
                  f"{str(r['is_test']):>5}")

        print("\nLatest 10 minutes of analytics results (table: heart_rate_minute)")
        print(f"  {'minute':<17} {'bracelet':>8} {'average':>8} {'min':>5} {'max':>5}"
              f" {'used':>5} {'left out':>9}")
        minutes = db.execute(
            "SELECT to_char(minute_start, 'YYYY-MM-DD HH24:MI') AS time, device_id,"
            " average, min, max, used, removed"
            " FROM heart_rate_minute ORDER BY minute DESC LIMIT 10").fetchall()
        for m in reversed(minutes):
            average = "-" if m["average"] is None else f"{m['average']:.1f}"
            low = "-" if m["min"] is None else f"{m['min']:.0f}"
            high = "-" if m["max"] is None else f"{m['max']:.0f}"
            print(f"  {m['time']:<17} {m['device_id']:>8} {average:>8}"
                  f" {low:>5} {high:>5} {m['used']:>5} {m['removed']:>9}")
        if not minutes:
            print("  (none yet: open the Insights page or run the fake bracelet)")


if __name__ == "__main__":
    main()
