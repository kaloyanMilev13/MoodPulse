"""
MoodPulse - simulated data generator (analytics module, owner: Dobromir)

Creates realistic FAKE data that looks like what the real system will produce:
  - users.json          fake users, each with one bracelet (device)
  - readings.json       bracelet readings: heart rate, skin conductance, movement
  - journal.json        check-ins with a mood and an optional short text
  - answer_key.json     everything that was deliberately hidden in the data
                        (stress periods, exercise, faults, gaps ...) so the
                        analysis can be tested: "did my code find it?"

The whole team can use this data until the real bracelet and website work
(team rule 1: everyone starts with simulated data).

Usage (from the analytics/ folder):
    python src/generate_sample_data.py
    python src/generate_sample_data.py --users 5 --days 14 --interval 60 --seed 1

The same --seed always gives exactly the same data, so results are repeatable.
"""

import argparse
import json
import math
import random
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Amsterdam")
except Exception:  # Windows without the tzdata package
    raise SystemExit("Time zone data missing. Run:  pip install tzdata")


# ---------------------------------------------------------------------------
# Agreed data format (see analytics/README.md)
# ---------------------------------------------------------------------------
UNITS = {
    "heart_rate": "BPM",          # integer
    "skin_conductance": "µS",     # number, 2 decimals
    "movement": "g",              # acceleration above gravity, 3 decimals
}

MOODS = ["happy", "calm", "neutral", "tired", "stressed", "anxious", "sad"]

JOURNAL_TEXTS = {
    "happy": ["Good day, got a lot done.", "Nice evening with friends.",
              "Feeling energetic after training."],
    "calm": ["Quiet day, took it slow.", "Relaxed, read a book tonight.",
             "Felt balanced today."],
    "neutral": ["Normal day.", "Nothing special happened.",
                "Classes and some homework."],
    "tired": ["Slept badly, tired all day.", "Long day, need rest.",
              "Low energy this afternoon."],
    "stressed": ["Deadline tomorrow, feeling tense.",
                 "Too many assignments at once.",
                 "Presentation went badly, still stressed."],
    "anxious": ["Worried about the exam.", "Could not stop overthinking.",
                "Nervous about the group meeting."],
    "sad": ["Feeling a bit down today.", "Missed home today.",
            "Not a great day."],
}

# Fixed local (Amsterdam) time slots for the planted events. They never
# overlap, which keeps the answer key easy to read and to test against.
SLOT_NOT_WORN = ((7, 30), (8, 0))         # every morning: shower
SLOT_STRESS = ((13, 0), (17, 0))          # on stress days
SLOT_EXERCISE = ((18, 0), (19, 0))        # on exercise days
SLOT_FLATLINE = ((10, 0), (12, 0))        # skin conductance stuck
SLOT_CONNECTION_GAP = ((20, 0), (23, 0))  # buffered, uploaded late
SLOT_NO_DATA = ((1, 0), (3, 0))           # battery empty: nothing recorded
SLEEP_START_HOUR, SLEEP_END_HOUR = 23, 7


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def to_iso(dt):
    """UTC datetime -> ISO 8601 string ending in Z, e.g. 2026-11-05T09:15:00Z"""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_uuid(rng):
    """UUID made from our seeded random generator, so it is repeatable."""
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def local_window(day, slot):
    """Day + ((h, m), (h, m)) in Amsterdam time -> (start, end) in UTC."""
    (h1, m1), (h2, m2) = slot
    start = datetime(day.year, day.month, day.day, h1, m1, tzinfo=LOCAL_TZ)
    end = datetime(day.year, day.month, day.day, h2, m2, tzinfo=LOCAL_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def inside(t, window):
    return window[0] <= t < window[1]


def event(user_id, kind, window, **details):
    return {"user_id": user_id, "type": kind,
            "start": to_iso(window[0]), "end": to_iso(window[1]), **details}


# ---------------------------------------------------------------------------
# 1. Users and their personal "normal" values
# ---------------------------------------------------------------------------
def make_users(n_users, rng):
    users, profiles = [], []
    for i in range(1, n_users + 1):
        user = {
            "user_id": make_uuid(rng),
            "username": f"test_user_{i}",
            "device_id": make_uuid(rng),
            "timezone": "Europe/Amsterdam",
        }
        users.append(user)
        # Hidden true baseline - the analysis should find values close to it.
        profiles.append({
            "user_id": user["user_id"],
            "username": user["username"],
            "true_resting_heart_rate": rng.randint(58, 72),
            "true_skin_conductance_base": round(rng.uniform(2.0, 5.0), 2),
        })
    return users, profiles


# ---------------------------------------------------------------------------
# 2. Plan which special things happen on which day
# ---------------------------------------------------------------------------
def plan_events(user, days, rng):
    """Return the planted windows for one user (all times in UTC)."""
    uid = user["user_id"]
    day_list = list(days)
    shuffled = day_list[:]
    rng.shuffle(shuffled)

    n_special = max(1, len(day_list) // 3)
    stress_days = sorted(shuffled[:n_special])
    exercise_days = sorted(shuffled[n_special:2 * n_special]) or [shuffled[-1]]

    plan = {
        "not_worn": [local_window(d, SLOT_NOT_WORN) for d in day_list],
        "stress": [local_window(d, SLOT_STRESS) for d in stress_days],
        "exercise": [local_window(d, SLOT_EXERCISE) for d in exercise_days],
        "flatline": [local_window(rng.choice(day_list), SLOT_FLATLINE)],
        "connection_gap": [local_window(rng.choice(day_list),
                                        SLOT_CONNECTION_GAP)],
        "no_data": [local_window(rng.choice(day_list), SLOT_NO_DATA)],
        "stress_days": stress_days,
        "exercise_days": exercise_days,
    }

    descriptions = {
        "not_worn": "Bracelet taken off: heart rate unavailable, skin "
                    "conductance near 0, no movement.",
        "stress": "Raised heart rate and skin conductance while sitting still.",
        "exercise": "High movement with high heart rate - NOT stress.",
        "flatline": "Skin conductance stuck at exactly one value (sensor fault).",
        "connection_gap": "Readings were buffered on the bracelet and uploaded "
                          "after the window (received_at much later than "
                          "measured_at).",
        "no_data": "Battery empty: no readings exist for this window.",
    }
    events = [event(uid, kind, w, description=descriptions[kind])
              for kind in descriptions for w in plan[kind]]
    return plan, events


# ---------------------------------------------------------------------------
# 3. Sensor readings
# ---------------------------------------------------------------------------
def state_at(t, plan):
    """What is the user doing at UTC time t?"""
    if any(inside(t, w) for w in plan["not_worn"]):
        return "not_worn"
    if any(inside(t, w) for w in plan["exercise"]):
        return "exercise"
    if any(inside(t, w) for w in plan["stress"]):
        return "stress"
    hour = t.astimezone(LOCAL_TZ).hour
    if hour >= SLEEP_START_HOUR or hour < SLEEP_END_HOUR:
        return "sleep"
    return "awake"


def sensor_values(state, t, profile, rng):
    """Realistic heart rate, skin conductance and movement for one moment."""
    hr = profile["true_resting_heart_rate"]
    sc = profile["true_skin_conductance_base"]
    local_hour = t.astimezone(LOCAL_TZ).hour + t.minute / 60

    if state == "sleep":
        hr, sc, mv = hr - 8, sc * 0.6, 0.005
    elif state == "exercise":
        hr, sc, mv = hr + 60, sc + 2.0, 0.6
    elif state == "stress":
        hr, sc, mv = hr + 18, sc + 3.0, 0.03
    else:  # awake: a gentle daily rhythm, highest mid-afternoon
        hr += 8 + 4 * math.sin((local_hour - 9) / 24 * 2 * math.pi)
        sc += 0.5
        mv = 0.05

    hr = int(round(hr + rng.gauss(0, 3)))
    sc = round(max(0.1, sc + rng.gauss(0, 0.2)), 2)
    mv = round(max(0.0, mv + abs(rng.gauss(0, 0.02))), 3)
    return {"heart_rate": hr, "skin_conductance": sc, "movement": mv}


def make_readings(user, profile, plan, start_utc, end_utc, interval, rng):
    readings = []
    flat_value = round(profile["true_skin_conductance_base"] + 0.5, 2)
    gap = plan["connection_gap"][0]

    t = start_utc
    while t < end_utc:
        if inside(t, plan["no_data"][0]):
            t += timedelta(seconds=interval)
            continue

        state = state_at(t, plan)
        values = sensor_values(state, t, profile, rng)

        # Normally the server receives a reading a few seconds after it was
        # measured. During a connection gap it arrives after the gap ends.
        if inside(t, gap):
            received = gap[1] + timedelta(seconds=rng.randint(30, 120))
        else:
            received = t + timedelta(seconds=rng.randint(1, 5))

        for kind in ("heart_rate", "skin_conductance", "movement"):
            value, status = values[kind], "valid"
            if state == "not_worn":
                if kind == "heart_rate":
                    value, status = None, "unavailable"  # firmware (EMB-03)
                elif kind == "skin_conductance":
                    value = round(rng.uniform(0.0, 0.05), 2)
                else:
                    value = 0.0
            if kind == "skin_conductance" and inside(t, plan["flatline"][0]):
                value = flat_value

            readings.append({
                "reading_id": make_uuid(rng),
                "device_id": user["device_id"],
                "type": kind,
                "value": value,
                "unit": UNITS[kind],
                "status": status,
                "measured_at": to_iso(t),
                "received_at": to_iso(received),
            })
        t += timedelta(seconds=interval)
    return readings


def add_faults(user, readings, rng):
    """Hide single faulty readings that the analysis should catch."""
    uid, events = user["user_id"], []

    # a) Impossible heart-rate spikes (one too high, one too low)
    candidates = [r for r in readings
                  if r["type"] == "heart_rate" and r["status"] == "valid"]
    spiked = set()
    for r, bad_value in zip(rng.sample(candidates, 2), (230, 25)):
        events.append({"user_id": uid, "type": "impossible_value",
                       "reading_type": "heart_rate",
                       "measured_at": r["measured_at"],
                       "original_value": r["value"], "fake_value": bad_value})
        r["value"] = bad_value
        spiked.add(r["reading_id"])

    # b) Readings that firmware marked as failed (random sensor hiccups)
    valid = [r for r in readings
             if r["status"] == "valid" and r["reading_id"] not in spiked]
    for r in rng.sample(valid, max(1, len(valid) // 400)):
        r["value"], r["status"] = None, "unavailable"
        events.append({"user_id": uid, "type": "unavailable_reading",
                       "reading_type": r["type"],
                       "measured_at": r["measured_at"]})

    # c) Duplicates: the same measurement sent twice (new reading_id,
    #    because the server creates a new ID for every row it receives)
    for r in rng.sample(readings, 3):
        copy = dict(r, reading_id=make_uuid(rng))
        received = datetime.strptime(r["received_at"], "%Y-%m-%dT%H:%M:%SZ")
        copy["received_at"] = to_iso(
            received.replace(tzinfo=timezone.utc) + timedelta(seconds=7))
        readings.append(copy)
        events.append({"user_id": uid, "type": "duplicate",
                       "reading_type": r["type"],
                       "measured_at": r["measured_at"]})
    return events


# ---------------------------------------------------------------------------
# 4. Journal entries
# ---------------------------------------------------------------------------
def pick_mood(day, plan, rng):
    if day in plan["stress_days"]:
        return rng.choice(["stressed", "stressed", "anxious"])
    if day in plan["exercise_days"]:
        return rng.choice(["happy", "happy", "calm", "tired"])
    return rng.choices(MOODS, weights=[2, 3, 4, 2, 1, 1, 1])[0]


def make_journal(user, days, plan, rng):
    entries = []
    for day in days:
        # The journal is optional: some days have no entry. Stress days always
        # have one, so the link "stress in the data <-> stressed mood" exists.
        if day not in plan["stress_days"] and rng.random() < 0.15:
            continue
        mood = pick_mood(day, plan, rng)
        local = datetime(day.year, day.month, day.day, 20, 30,
                         tzinfo=LOCAL_TZ) + timedelta(
                             minutes=rng.randint(0, 90))
        text = rng.choice(JOURNAL_TEXTS[mood]) if rng.random() < 0.75 else None
        entries.append({
            "entry_id": make_uuid(rng),
            "user_id": user["user_id"],
            "mood": mood,
            "text": text,
            "created_at": to_iso(local),
        })
    return entries


# ---------------------------------------------------------------------------
# 5. Put it all together
# ---------------------------------------------------------------------------
def generate(n_users=3, n_days=7, interval=300, seed=42, start=None):
    """Build the full dataset and return it as a dictionary of lists."""
    rng = random.Random(seed)
    if start is None:
        start = date.today() - timedelta(days=n_days)
    days = [start + timedelta(days=i) for i in range(n_days)]
    start_utc = datetime(start.year, start.month, start.day,
                         tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    end = start + timedelta(days=n_days)
    end_utc = datetime(end.year, end.month, end.day,
                       tzinfo=LOCAL_TZ).astimezone(timezone.utc)

    users, profiles = make_users(n_users, rng)
    readings, journal, events = [], [], []
    for user, profile in zip(users, profiles):
        plan, planned_events = plan_events(user, days, rng)
        user_readings = make_readings(user, profile, plan, start_utc,
                                      end_utc, interval, rng)
        events += planned_events + add_faults(user, user_readings, rng)
        readings += user_readings
        journal += make_journal(user, days, plan, rng)

    # The server stores readings in the order they ARRIVE, not the order they
    # were measured - that's why late uploads end up "out of order".
    readings.sort(key=lambda r: (r["received_at"], r["device_id"]))
    journal.sort(key=lambda e: e["created_at"])
    events.sort(key=lambda e: (e["user_id"], e.get("start") or e["measured_at"]))

    answer_key = {
        "settings": {"users": n_users, "days": n_days,
                     "interval_seconds": interval, "seed": seed,
                     "start_date": start.isoformat(),
                     "local_timezone": "Europe/Amsterdam"},
        "profiles": profiles,
        "events": events,
    }
    return {"users": users, "readings": readings,
            "journal": journal, "answer_key": answer_key}


def save(data, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in data.items():
        with open(out_dir / f"{name}.json", "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2, ensure_ascii=False)


def print_summary(data, out_dir):
    events = data["answer_key"]["events"]
    counts = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    print(f"Saved to {Path(out_dir).resolve()}")
    print(f"  users:    {len(data['users'])}")
    print(f"  readings: {len(data['readings'])}")
    print(f"  journal:  {len(data['journal'])}")
    print("  planted events (see answer_key.json):")
    for kind, n in sorted(counts.items()):
        print(f"    {kind:<20} {n}")


def main():
    parser = argparse.ArgumentParser(description="Generate MoodPulse test data")
    parser.add_argument("--users", type=int, default=3)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--interval", type=int, default=300,
                        help="seconds between readings (default 300 = 5 min)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start", type=date.fromisoformat, default=None,
                        help="first day, YYYY-MM-DD (default: a week ago)")
    parser.add_argument("--out", default="sample_data")
    args = parser.parse_args()

    if args.days < 3:
        parser.error("--days must be at least 3")
    data = generate(args.users, args.days, args.interval, args.seed, args.start)
    save(data, args.out)
    print_summary(data, args.out)


if __name__ == "__main__":
    main()
