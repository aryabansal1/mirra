# Mirra

A web version of the wearable breast-health monitoring architecture described in
*An introduction to the Cyrcadia Breast Monitor* (Sree et al., 2020, Fig. 4), using
synthetic data and a placeholder analytics model.

## Architecture

```
ESP32 patch (local buffer) --Bluetooth--> Website (static HTML/JS) --POST /api/device/sync--> Data Manager API (FastAPI)
  simulated by data/device_buffer.csv      simulated device in the browser                       |         |
                                                                                                   v         v
                                                              PAA Analytics service (FastAPI)            Postgres
                                                              POST /analyze -> result                    - patients, scans  (Local Patient DB)
                                                                                                         - analysis_results (Core Lab DB)
```

| Paper component                | This repo                                               |
| ------------------------------ | ------------------------------------------------------- |
| Wearable patch + recorder      | Simulated ESP32 whose stored readings are `data/device_buffer.csv` |
| Bluetooth transfer             | Ack-based device sync (below), simulated in `data-manager/app/static/device.js` |
| Mobile app                     | Website served by the data manager (`data-manager/app/static`) |
| Cyrcadia local data manager    | `data-manager/` (FastAPI)                              |
| Local Patient Database         | Postgres tables `patients`, `scans`                    |
| PAA Analytics                  | `analytics/` (separate FastAPI service)                |
| Core Lab Database              | Postgres table `analysis_results`                      |

### Flow
1. Click **Sync from device**. The device sends every reading the website has not acknowledged yet.
2. The data manager stores new readings (`status = uploaded`), ignores duplicates, and acks every reading it received.
   It then calls `POST {ANALYTICS_URL}/analyze` for each new reading (`status = analyzing`).
3. The result is stored in `analysis_results` and the scan becomes `results_ready` (or `failed`).
4. The website shows the device state, counts, the scan list, and per-scan results.

## Bluetooth sync

The device keeps each reading in local storage until the website confirms it, so an interrupted sync can resume
without losing or double-counting readings.

**Reading format** (one temperature reading per scan, also the columns of `data/device_buffer.csv`):

| field           | example            | notes                                      |
| --------------- | ------------------ | ------------------------------------------ |
| `device_id`     | `ESP32-01`         | sent once per batch in the JSON body       |
| `session_id`    | `S0008`            | recording session on the device            |
| `seq`           | `8`                | increasing sequence number per device      |
| `recorded_at`   | `2026-10-03T16:00` | ISO 8601 timestamp                         |
| `patient_uid`   | `0852-032-008`     |                                            |
| `patch_size`    | `3`                | 1–6                                        |
| `temperature_c` | `36.07`            | 20–45 °C                                   |

**Protocol**

1. The device sends unconfirmed readings in chunks (at most 500 per request):
   ```http
   POST /api/device/sync
   {"device_id": "ESP32-01", "readings": [{"session_id": "S0008", "seq": 8, "recorded_at": "2026-10-03T16:00",
     "patient_uid": "0852-032-008", "patch_size": 3, "temperature_c": 36.07}]}
   ```
2. The server stores readings it hasn't seen, keyed on `(device_id, session_id, seq)`, and replies with an ack
   for **every** reading in the request, including duplicates:
   ```json
   {"accepted": 1, "duplicates": 0, "acks": [{"session_id": "S0008", "seq": 8}]}
   ```
3. The device deletes (or marks confirmed) only the acked readings. If the connection drops before the ack arrives,
   it resends those readings next time; the server ignores them as duplicates and acks them again.

`GET /api/devices/{device_id}` returns how many readings the server has from a device, its last `seq` and the last
sync time.

**In this repo** the ESP32 is simulated in the browser: `GET /api/simulated-device/buffer` serves
`data/device_buffer.csv` as the device's memory, and confirmed readings are remembered in `localStorage`. The
dashboard has **Record new reading** (the device takes a new reading), **Simulate connection drop** (the second
chunk's ack is lost, so the next sync resends it) and **Reset device memory** (resend everything; all duplicates).
For real hardware, replace `SimulatedBluetoothDevice` in `device.js` with a Web Bluetooth (Chrome) client that
reads unconfirmed readings from the ESP32 and writes the acks back; `syncDevice()` stays the same.

**Upload CSV** accepts a file in the same format as `data/device_buffer.csv` (one `device_id` per group of rows)
and goes through the same idempotent ingest.

Regenerate the synthetic buffer with `python scripts/generate_synthetic_data.py`.

## Analytics model

`analytics/app/model.py`: a scan is flagged **"Abnormal - suspected malignancy"** when
`temperature_c > 35.0 °C`, otherwise **"Normal"**. Healthy breast skin is typically ~33–34 °C and
tumors raise local skin temperature by roughly 1–2 °C, so 35.0 °C sits between the two. This is a
placeholder, not a clinically validated threshold.

**Swapping in an ML model:** replace `predict()` in `analytics/app/model.py` (load your model at import
time, add dependencies to `analytics/requirements.txt`) and bump `MODEL_VERSION`. As long as it returns
`abnormal`, `label`, `threshold_c`, `model_version`, nothing else needs to change. If the model needs more
inputs (e.g. a full temperature time series), extend `AnalyzeRequest`, the reading format and the `scans` schema.

## Run locally

```bash
docker compose up --build
# open http://localhost:8000
```

Services: website + API on `:8000`, analytics on `:8001`, Postgres on `:5432`.

## Tests

```bash
pip install -r analytics/requirements.txt -r data-manager/requirements.txt pytest
(cd analytics && pytest) && (cd data-manager && pytest)
```

## Deploy (Render)

`render.yaml` is a Render Blueprint that creates a Postgres database, the analytics service and the
website/data manager. In Render: **New → Blueprint**, select this repo, and apply. The website URL is the
`mirra-web` service URL.

> Demo only: synthetic data and a placeholder model. Not a medical device.
