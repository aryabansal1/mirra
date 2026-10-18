# Build context is the repo root so the synthetic CSV can be bundled.
FROM python:3.12-slim
WORKDIR /srv/data-manager
COPY data-manager/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY data-manager/app ./app
COPY data /srv/data
ENV PORT=8000 SYNC_CSV_PATH=/srv/data/synthetic_scans.csv
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
