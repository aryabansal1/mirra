"""Generate the synthetic scan CSV in data/synthetic_scans.csv.

Each row is one scan from the wearable patch that has already been transferred
over Bluetooth: a single breast-skin temperature reading per scan.
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "synthetic_scans.csv"
NUM_PATIENTS = 20
SEED = 42


def main() -> None:
    rng = random.Random(SEED)
    start = datetime(2026, 10, 1, 8, 0)
    rows = []
    for i in range(1, NUM_PATIENTS + 1):
        abnormal = rng.random() < 0.4
        temperature = rng.gauss(35.8, 0.4) if abnormal else rng.gauss(33.8, 0.5)
        rows.append(
            {
                "patient_uid": f"0852-032-{i:03d}",
                "recorder_id": f"DRD-{rng.randint(1000, 9999)}",
                "patch_size": rng.randint(1, 6),
                "scan_start": (start + timedelta(hours=7 * i)).isoformat(timespec="minutes"),
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
