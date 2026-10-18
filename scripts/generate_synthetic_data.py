"""Generate data/device_buffer.csv.

The CSV is the simulated ESP32 device buffer: readings stored on the device
before they are synced to the website over Bluetooth. Each session is one scan
with a single breast-skin temperature reading; `seq` increases per device.
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "device_buffer.csv"
NUM_PATIENTS = 20
DEVICE_ID = "ESP32-01"
SEED = 42


def main() -> None:
    rng = random.Random(SEED)
    start = datetime(2026, 10, 1, 8, 0)
    rows = []
    for i in range(1, NUM_PATIENTS + 1):
        abnormal = rng.random() < 0.4
        temperature = rng.gauss(35.8, 0.4) if abnormal else rng.gauss(33.8, 0.5)
        rng.randint(1000, 9999)  # keeps the RNG sequence (and temperatures) stable
        rows.append(
            {
                "device_id": DEVICE_ID,
                "session_id": f"S{i:04d}",
                "seq": i,
                "recorded_at": (start + timedelta(hours=7 * i)).isoformat(timespec="minutes"),
                "patient_uid": f"0852-032-{i:03d}",
                "patch_size": rng.randint(1, 6),
                "temperature_c": round(temperature, 2),
            }
        )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUTPUT}")


if __name__ == "__main__":
    main()
