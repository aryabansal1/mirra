"""Generate the synthetic data in data/.

- device_buffer.csv: readings stored on the demo patient's ESP32-01, waiting to be synced over Bluetooth.
  The device doesn't know who the patient is; the website assigns readings to the patient whose account
  the device is connected to.
- clinic_upload.csv: a batch of readings from other patients' devices, for the admin "Upload CSV".

Each session is one scan with a single breast-skin temperature reading; `seq` increases per device.
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
SEED = 42
DEMO_DEVICE = "ESP32-01"
DEMO_TEMPS = [33.92, 34.08, 33.85, 34.31, 35.42, 35.71]
CLINIC_PATIENTS = range(2, 22)


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")


def main() -> None:
    rng = random.Random(SEED)
    start = datetime(2026, 10, 1, 8, 0)

    device = [
        {
            "device_id": DEMO_DEVICE,
            "session_id": f"S{seq}",
            "seq": seq,
            "recorded_at": (start + timedelta(days=2 * (seq - 1))).isoformat(timespec="minutes"),
            "temperature_c": temp,
        }
        for seq, temp in enumerate(DEMO_TEMPS, start=1)
    ]
    write(DATA / "device_buffer.csv", device)

    clinic = []
    for i in CLINIC_PATIENTS:
        abnormal = rng.random() < 0.3
        temperature = rng.gauss(35.8, 0.4) if abnormal else rng.gauss(33.8, 0.5)
        clinic.append(
            {
                "patient_uid": f"0852-032-{i:03d}",
                "patch_size": rng.randint(1, 6),
                "device_id": f"ESP32-{i:02d}",
                "session_id": "S1",
                "seq": 1,
                "recorded_at": (start + timedelta(hours=7 * i)).isoformat(timespec="minutes"),
                "temperature_c": round(temperature, 2),
            }
        )
    write(DATA / "clinic_upload.csv", clinic)


if __name__ == "__main__":
    main()
