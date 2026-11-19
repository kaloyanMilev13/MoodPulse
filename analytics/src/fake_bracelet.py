"""
MoodPulse - fake bracelet (analytics module, owner: Dobromir)

Sends heart-rate readings to the backend exactly like the website does after
it receives them from the real bracelet over Bluetooth. Use it to try the
whole product without the hardware.

The heart rate counts up from 41 to 180 and starts again, the same as the
bracelet's own simulation mode, and every reading has the same flag switches
as the real firmware in simulation mode (simulation + buffering).

Start the backend first, then (from the project folder):
    python analytics/src/fake_bracelet.py                 60 readings, one per second
    python analytics/src/fake_bracelet.py --fast          60 readings at once
    python analytics/src/fake_bracelet.py --problems      also send faulty readings
                                                          and a connection drop
"""

import argparse
import json
import time
import urllib.error
import urllib.request

DEVICE_ID = 4660
BATCH = 500   # the server accepts at most 500 readings per request

# Flag switches, the same values as in the firmware (firmware/xiao/xiao.ino)
FLAG_SENSOR_ERROR = 0x02
FLAG_POOR_WRIST_CONTACT = 0x10
FLAG_OFFLINE_BUFFERING = 0x20
FLAG_SIMULATION_MODE = 0x40
NORMAL = FLAG_SIMULATION_MODE | FLAG_OFFLINE_BUFFERING   # every simulated packet

# With --problems, the connection "drops" for these readings: the bracelet
# keeps them in its buffer and sends them together with the next one.
DROP = range(30, 40)


def post(url, body):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def make_readings(count, problems):
    """Build the readings the bracelet would send, one per second."""
    readings = []
    bpm = 40
    start = time.time() - count
    # A real bracelet has a different uptime after every restart; do the
    # same here, so a second run is not mistaken for resent readings.
    uptime_start = int(time.time() * 1000) % 1_000_000_000
    for sequence in range(1, count + 1):
        bpm = 41 if bpm >= 180 else bpm + 1
        readings.append({
            "type": "heart_rate", "value": bpm, "ts": start + sequence,
            "sequence": sequence, "uptime_ms": uptime_start + sequence * 1000,
            "device_id": DEVICE_ID, "is_test": 1, "flags": NORMAL, "battery": 87,
        })

    if problems and count >= 45:
        readings[4]["flags"] = NORMAL | FLAG_SENSOR_ERROR   # sensor error
        readings[9]["flags"] = NORMAL | FLAG_POOR_WRIST_CONTACT  # poor wrist contact
        readings.insert(15, dict(readings[14]))              # the same reading twice
        del readings[17:20]                                  # three readings never arrive
        # Connection drop: these readings arrive late, all just before the
        # next reading, the way the firmware empties its buffer.
        flush = next(r for r in readings if r["sequence"] == DROP[-1] + 1)["ts"]
        for r in readings:
            if r["sequence"] in DROP:
                r["ts"] = flush - 0.3 + (r["sequence"] - DROP[0]) * 0.02
    return readings


def main():
    parser = argparse.ArgumentParser(description="Send fake bracelet readings")
    # 127.0.0.1 instead of "localhost": on Windows, "localhost" first tries
    # the IPv6 address, which the backend does not listen on, and every
    # reading then waits about 2 seconds before it is sent.
    parser.add_argument("--url", default="http://127.0.0.1:5000")
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--fast", action="store_true",
                        help="send everything at once instead of one per second")
    parser.add_argument("--problems", action="store_true",
                        help="include a sensor error, poor wrist contact, a duplicate, "
                             "three lost readings and a 10-second connection drop")
    args = parser.parse_args()

    readings = make_readings(args.count, args.problems)
    try:
        post(args.url + "/api/sensor-session", {"active": True})
        if args.fast:
            for start in range(0, len(readings), BATCH):
                post(args.url + "/api/sensors", {"readings": readings[start:start + BATCH]})
            print(f"Sent {len(readings)} readings.")
        else:
            waiting = []   # readings held back during a connection drop
            for reading in readings:
                if args.problems and reading["sequence"] in DROP:
                    waiting.append(reading)
                    print(f"Connection lost: {reading['value']} BPM kept in the buffer "
                          f"(sequence {reading['sequence']})")
                    time.sleep(1)
                    continue
                now = time.time()
                batch = waiting + [reading]
                for n, r in enumerate(batch):
                    r["ts"] = now - (len(batch) - 1 - n) * 0.02
                post(args.url + "/api/sensors", {"readings": batch})
                if waiting:
                    print(f"Connection back: sent {len(waiting)} buffered readings")
                    waiting = []
                print(f"Sent {reading['value']} BPM (sequence {reading['sequence']})")
                time.sleep(1)
        print("Summary from the backend:")
        with urllib.request.urlopen(args.url + "/api/heart-summary") as response:
            print(" ", json.load(response)["summary"])
    except urllib.error.HTTPError as error:
        print(f"The backend refused the readings: HTTP {error.code} {error.read().decode()[:200]}")
    except urllib.error.URLError as error:
        print(f"Could not reach the backend at {args.url}: {error.reason}")
        print("Start it first with:  python backend/app.py")


if __name__ == "__main__":
    main()
