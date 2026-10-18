# Mirra

A web version of the wearable breast-health monitoring architecture described in
*An introduction to the Cyrcadia Breast Monitor* (Sree et al., 2020, Fig. 4), using
synthetic data and a placeholder analytics model.

## Architecture

```
Wearable patch --Bluetooth--> data/synthetic_scans.csv (assumed already transferred)
                                   |
                                   v  (Synchronize / Upload CSV)
Website (static HTML/JS) <---> Data Manager API (FastAPI) <---> Postgres
                                   |                            - patients, scans    (Local Patient DB)
                                   v                            - analysis_results   (Core Lab DB)
                       PAA Analytics service (FastAPI)
                       POST /analyze -> result
```

| Paper component                | This repo                                               |
| ------------------------------ | ------------------------------------------------------- |
| Wearable patch + recorder      | `data/synthetic_scans.csv` (assumed already transferred via Bluetooth) |
| Mobile app                     | Website served by the data manager (`data-manager/app/static`) |
| Cyrcadia local data manager    | `data-manager/` (FastAPI)                              |
| Local Patient Database         | Postgres tables `patients`, `scans`                    |
| PAA Analytics                  | `analytics/` (separate FastAPI service)                |
| Core Lab Database              | Postgres table `analysis_results`                      |

### Flow
1. Click **Synchronize scan data** (imports `data/synthetic_scans.csv`) or **Upload CSV**.
2. The data manager stores patients and scans (`status = uploaded`), then for each scan calls
   `POST {ANALYTICS_URL}/analyze` (`status = analyzing`).
3. The result is stored in `analysis_results` and the scan becomes `results_ready` (or `failed`).
4. The website shows counts, the scan list, and per-scan results.

## Synthetic data

`data/synthetic_scans.csv` has one temperature reading per scan:

| column          | example            |
| --------------- | ------------------ |
| `patient_uid`   | `0852-032-008`     |
| `recorder_id`   | `DRD-2584`         |
| `patch_size`    | `3` (1–6)          |
| `scan_start`    | `2026-10-03T16:00` |
| `temperature_c` | `36.07`            |

Regenerate with `python scripts/generate_synthetic_data.py`.

## Analytics model

`analytics/app/model.py`: a scan is flagged **"Abnormal - suspected malignancy"** when
`temperature_c > 35.0 °C`, otherwise **"Normal"**. Healthy breast skin is typically ~33–34 °C and
tumors raise local skin temperature by roughly 1–2 °C, so 35.0 °C sits between the two. This is a
placeholder, not a clinically validated threshold.

**Swapping in an ML model:** replace `predict()` in `analytics/app/model.py` (load your model at import
time, add dependencies to `analytics/requirements.txt`) and bump `MODEL_VERSION`. As long as it returns
`abnormal`, `label`, `threshold_c`, `model_version`, nothing else needs to change. If the model needs more
inputs (e.g. a full temperature time series), extend `AnalyzeRequest` and the CSV/`scans` schema.

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
