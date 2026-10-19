from fastapi.testclient import TestClient

from app.main import app
from app.model import THRESHOLD_C

client = TestClient(app)


def test_above_threshold_is_abnormal():
    r = client.post("/analyze", json={"scan_id": 1, "temperature_c": THRESHOLD_C + 0.1})
    assert r.status_code == 200
    assert r.json()["abnormal"] is True


def test_at_or_below_threshold_is_normal():
    for t in (THRESHOLD_C, 33.5):
        assert client.post("/analyze", json={"temperature_c": t}).json()["abnormal"] is False
