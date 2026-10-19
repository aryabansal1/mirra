import csv
import os
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test_mirra.db"

import pytest
from fastapi.testclient import TestClient

from app import analytics_client, main
from app.auth import hash_password
from app.db import Base, Patient, SessionLocal, User, engine

CLINIC_CSV = Path(__file__).resolve().parents[2] / "data" / "clinic_upload.csv"


@pytest.fixture()
def client(monkeypatch):
    Base.metadata.drop_all(engine)

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


def login(client, username, password, role):
    r = client.post("/api/auth/login", json={"username": username, "password": password, "role": role})
    assert r.status_code == 200, r.text


def connect(client, device_id="ESP32-01"):
    assert client.post("/api/me/device", json={"device_id": device_id}).status_code == 200


def reading(seq, temp=34.0, session="S1"):
    return {"session_id": session, "seq": seq, "recorded_at": "2026-10-01T08:00", "temperature_c": temp}


def test_logins_are_role_specific(client):
    r = client.post("/api/auth/login", json={"username": "patient", "password": "patient", "role": "admin"})
    assert r.status_code == 401
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong", "role": "admin"})
    assert r.status_code == 401
    assert client.get("/api/summary").status_code == 401

    login(client, "patient", "patient", "patient")
    assert client.get("/api/summary").status_code == 403
    assert client.get("/api/me").json()["patient_uid"] == "0852-032-001"

    client.post("/api/auth/logout")
    login(client, "admin", "admin", "admin")
    assert client.get("/api/me/scans").status_code == 403
    assert client.get("/api/summary").status_code == 200


def test_patient_sync_acks_everything_and_dedupes(client):
    login(client, "patient", "patient", "patient")
    connect(client)
    body = {"device_id": "ESP32-01", "readings": [reading(1, 36.0), reading(2)]}
    r = client.post("/api/device/sync", json=body).json()
    assert (r["accepted"], r["duplicates"]) == (2, 0)
    assert r["acks"] == [{"session_id": "S1", "seq": 1}, {"session_id": "S1", "seq": 2}]

    # Resend after a lost ack: duplicates are acked again but not stored twice.
    body["readings"].append(reading(3))
    r = client.post("/api/device/sync", json=body).json()
    assert (r["accepted"], r["duplicates"], len(r["acks"])) == (1, 2, 3)

    scans = client.get("/api/me/scans").json()
    assert len(scans) == 3 and all(s["patient_uid"] == "0852-032-001" for s in scans)
    assert sum(s["result"]["abnormal"] for s in scans) == 1


def test_sync_requires_device_connected_to_own_account(client):
    login(client, "patient", "patient", "patient")
    r = client.post("/api/device/sync", json={"device_id": "ESP32-01", "readings": [reading(1)]})
    assert r.status_code == 403

    with SessionLocal() as db:
        db.add(Patient(uid="P-2", patch_size=1))
        db.flush()
        db.add(User(username="other", password_hash=hash_password("pw"), role="patient", patient_uid="P-2"))
        db.commit()
    connect(client)
    client.post("/api/auth/logout")
    login(client, "other", "pw", "patient")
    assert client.post("/api/me/device", json={"device_id": "ESP32-01"}).status_code == 409
    assert client.post("/api/device/sync", json={"device_id": "ESP32-01", "readings": [reading(1)]}).status_code == 403


def test_simulated_buffer_syncs_end_to_end(client):
    login(client, "patient", "patient", "patient")
    buf = client.get("/api/simulated-device/buffer").json()
    connect(client, buf["device_id"])
    r = client.post("/api/device/sync", json=buf).json()
    assert r["accepted"] == len(buf["readings"]) == 6
    scans = client.get("/api/me/scans").json()
    assert sum(s["result"]["abnormal"] for s in scans) == sum(x["temperature_c"] > 35.0 for x in buf["readings"])


def test_admin_upload_is_not_visible_to_patient(client):
    login(client, "admin", "admin", "admin")
    r = client.post("/api/scans/upload", files={"file": ("c.csv", CLINIC_CSV.read_bytes())}).json()
    rows = list(csv.DictReader(CLINIC_CSV.open()))
    assert r["accepted"] == len(rows)
    s = client.get("/api/summary").json()
    assert s["abnormal"] == sum(float(x["temperature_c"]) > 35.0 for x in rows)
    assert s["patients"] == len(rows) + 1  # + the seeded demo patient

    client.post("/api/auth/logout")
    login(client, "patient", "patient", "patient")
    assert client.get("/api/me/scans").json() == []


def test_invalid_reading_rejected(client):
    login(client, "patient", "patient", "patient")
    connect(client)
    r = client.post("/api/device/sync", json={"device_id": "ESP32-01", "readings": [reading(1, temp=99)]})
    assert r.status_code == 422


def test_upload_rejects_missing_columns(client):
    login(client, "admin", "admin", "admin")
    r = client.post("/api/scans/upload", files={"file": ("x.csv", b"patient_uid,temperature_c\nA,36\n")})
    assert r.status_code == 400
