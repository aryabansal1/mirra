# Mirra

A web version of the wearable breast-health monitoring architecture described in
*An introduction to the Cyrcadia Breast Monitor* (Sree et al., 2020, Fig. 4), using
synthetic data and a placeholder analytics model.

## Architecture

```
ESP32 patch (local buffer) --Bluetooth--> Patient portal  (/)      --POST /api/device/sync--> Data Manager API (FastAPI)
  simulated by data/device_buffer.csv      Admin dashboard (/admin) --POST /api/scans/upload-->                      |         |
                                                                                                   v         v
                                                              PAA Analytics service (FastAPI)            Postgres
                                                              POST /analyze -> result                    - patients, scans  (Local Patient DB)
                                                                                                         - analysis_results (Core Lab DB)
```

| Paper component                | This repo                                               |
| ------------------------------ | ------------------------------------------------------- |
| Wearable patch + recorder      | Simulated ESP32 whose stored readings are `data/device_buffer.csv` |
| Bluetooth transfer             | Ack-based device sync (below), simulated in `data-manager/app/static/device.js` |
| Mobile app                     | Patient portal (`/`) and admin dashboard (`/admin`) served by the data manager (`data-manager/app/static`) |
| Cyrcadia local data manager    | `data-manager/` (FastAPI)                              |
| Local Patient Database         | Postgres tables `patients`, `scans`                    |
| PAA Analytics                  | `analytics/` (separate FastAPI service)                |
| Core Lab Database              | Postgres table `analysis_results`                      |

### Flow
1. **Patient** logs in at `/`, clicks **Connect device** once (links their ESP32 to their account), then **Sync**.
   The portal shows how many readings on the device haven't been synced yet.
2. **Admin** logs in at `/admin` and can **Upload CSV** (`data/clinic_upload.csv` is a sample). Admins see all
   patients and scans; patients only see their own readings and diagnosis.
3. The data manager stores new readings (`status = uploaded`), ignores duplicates, and acks every reading it received.
   It then calls `POST {ANALYTICS_URL}/analyze` for each new reading (`status = analyzing`).
4. The result is stored in `analysis_results` and the scan becomes `results_ready` (or `failed`).

### Accounts

Login is cookie-based (`data-manager/app/auth.py`), with separate patient and admin logins. On startup the data
manager creates two demo accounts if they don't exist:

| Role    | Username / password (env vars)                                   | Default           |
| ------- | ---------------------------------------------------------------- | ----------------- |
| Admin   | `ADMIN_USERNAME` / `ADMIN_PASSWORD`                              | `admin` / `admin` |
| Patient | `DEMO_PATIENT_USERNAME` / `DEMO_PATIENT_PASSWORD`, `DEMO_PATIENT_UID` | `patient` / `patient`, `0852-032-001` |

Change the defaults for any real deployment.

## Bluetooth sync

The device keeps each reading in local storage until the website confirms it, so an interrupted sync can resume
without losing or double-counting readings.

**Reading format** (one temperature reading per scan, also the columns of `data/device_buffer.csv`). The device
doesn't know who the patient is: readings belong to the patient whose account the device is connected to.

| field           | example            | notes                                      |
| --------------- | ------------------ | ------------------------------------------ |
| `device_id`     | `ESP32-01`         | sent once per batch in the JSON body       |
| `session_id`    | `S0008`            | recording session on the device            |
| `seq`           | `8`                | increasing sequence number per device      |
| `recorded_at`   | `2026-10-03T16:00` | ISO 8601 timestamp                         |
| `temperature_c` | `36.07`            | 20–45 °C                                   |

**Protocol**

1. The patient portal (logged in as the device's owner) sends unconfirmed readings in chunks (at most 500 per request):
   ```http
   POST /api/device/sync
   {"device_id": "ESP32-01", "readings": [{"session_id": "S0008", "seq": 8, "recorded_at": "2026-10-03T16:00",
     "temperature_c": 36.07}]}
   ```
2. The server stores readings it hasn't seen, keyed on `(device_id, session_id, seq)`, and replies with an ack
   for **every** reading in the request, including duplicates:
   ```json
   {"accepted": 1, "duplicates": 0, "acks": [{"session_id": "S0008", "seq": 8}]}
   ```
3. The device deletes (or marks confirmed) only the acked readings. If the connection drops before the ack arrives,
   it resends those readings next time; the server ignores them as duplicates and acks them again.

`POST /api/me/device {"device_id": ...}` links a device to the logged-in patient (one device per patient; `409` if
it's linked to someone else). Syncing a device that isn't linked to your account returns `403`.

**In this repo** the ESP32 is simulated in the browser: `GET /api/simulated-device/buffer` serves
`data/device_buffer.csv` as the device's memory, and confirmed readings are remembered in `localStorage`. For real
hardware, replace `SimulatedBluetoothDevice` in `device.js` with a Web Bluetooth (Chrome) client that reads
unconfirmed readings from the ESP32 and writes the acks back; `syncDevice()` stays the same.

**Admin Upload CSV** columns: `patient_uid, patch_size, device_id, session_id, seq, recorded_at, temperature_c`
(see `data/clinic_upload.csv`). It goes through the same idempotent ingest.

Regenerate both CSVs with `python scripts/generate_synthetic_data.py`.

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
# patient portal: http://localhost:8000        (patient / patient)
# admin dashboard: http://localhost:8000/admin (admin / admin)
```

There are no migrations yet: after pulling a schema change, reset the local database with `docker compose down -v`.

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
