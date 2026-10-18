import os

os.environ["DATABASE_URL"] = "sqlite:///./test_mirra.db"

import pytest
from fastapi.testclient import TestClient

from app import analytics_client, main
from app.db import Base, engine


@pytest.fixture()
def client(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    def fake_analyze(scan_id, temperature_c):
        abnormal = temperature_c > 35.0
        return {
            "abnormal": abnormal,
            "label": "Abnormal" if abnormal else "Normal",
            "threshold_c": 35.0,
            "model_version": "test",
        }

    monkeypatch.setattr(analytics_client, "analyze", fake_analyze)
    with TestClient(main.app) as c:
        yield c


def test_sync_ingests_and_analyzes_repo_csv(client):
    r = client.post("/api/scans/sync")
    assert r.status_code == 200
    assert r.json()["imported"] == 20
    s = client.get("/api/summary").json()
    assert s["results_ready"] == 20
    assert s["abnormal"] == sum(1 for x in client.get("/api/scans").json() if x["temperature_c"] > 35.0)
    assert client.post("/api/scans/sync").json() == {"imported": 0, "skipped_duplicates": 20}


def test_upload_rejects_missing_columns(client):
    r = client.post("/api/scans/upload", files={"file": ("x.csv", b"patient_uid,temperature_c\nA,36\n")})
    assert r.status_code == 400
