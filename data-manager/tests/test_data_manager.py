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


def reading(seq, temp=34.0, session="S1"):
    return {
        "session_id": session,
        "seq": seq,
        "recorded_at": "2026-10-01T08:00",
        "patient_uid": "P-1",
        "patch_size": 2,
        "temperature_c": temp,
    }


def test_device_sync_acks_everything_and_dedupes(client):
    body = {"device_id": "ESP32-01", "readings": [reading(1, 36.0), reading(2)]}
    r = client.post("/api/device/sync", json=body).json()
    assert (r["accepted"], r["duplicates"]) == (2, 0)
    assert r["acks"] == [{"session_id": "S1", "seq": 1}, {"session_id": "S1", "seq": 2}]

    # Resend after a lost ack: duplicates are acked again but not stored twice.
    body["readings"].append(reading(3))
    r = client.post("/api/device/sync", json=body).json()
    assert (r["accepted"], r["duplicates"], len(r["acks"])) == (1, 2, 3)

    s = client.get("/api/summary").json()
    assert (s["results_ready"], s["abnormal"]) == (3, 1)
    assert client.get("/api/devices/ESP32-01").json()["last_seq"] == 3


def test_same_seq_on_other_device_is_not_a_duplicate(client):
    client.post("/api/device/sync", json={"device_id": "A", "readings": [reading(1)]})
    r = client.post("/api/device/sync", json={"device_id": "B", "readings": [reading(1)]}).json()
    assert r["accepted"] == 1


def test_simulated_buffer_syncs_end_to_end(client):
    buf = client.get("/api/simulated-device/buffer").json()
    assert buf["device_id"] == "ESP32-01" and len(buf["readings"]) == 20
    r = client.post("/api/device/sync", json=buf).json()
    assert r["accepted"] == 20
    assert client.get("/api/summary").json()["abnormal"] == sum(x["temperature_c"] > 35.0 for x in buf["readings"])


def test_invalid_reading_rejected(client):
    r = client.post("/api/device/sync", json={"device_id": "A", "readings": [reading(1, temp=99)]})
    assert r.status_code == 422


def test_upload_rejects_missing_columns(client):
    r = client.post("/api/scans/upload", files={"file": ("x.csv", b"patient_uid,temperature_c\nA,36\n")})
    assert r.status_code == 400
