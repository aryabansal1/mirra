# Build context is the repo root so the synthetic CSV can be bundled.
FROM python:3.12-slim
WORKDIR /srv/data-manager
COPY data-manager/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY data-manager/app ./app
COPY data /srv/data
COPY analytics/app/model.py /srv/analytics/app/model.py
ENV PORT=8000 DEVICE_BUFFER_CSV=/srv/data/device_buffer.csv
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
