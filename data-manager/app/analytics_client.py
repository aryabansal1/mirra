import importlib.util
import os
from functools import cache
from pathlib import Path

import httpx

# Unset: run the PAA model in-process (single-service deploy). Set: call the separate analytics service.
ANALYTICS_URL = os.environ.get("ANALYTICS_URL", "")
PAA_MODEL_PATH = Path(
    os.environ.get("PAA_MODEL_PATH", Path(__file__).resolve().parents[2] / "analytics" / "app" / "model.py")
)


@cache
def _local_model():
    spec = importlib.util.spec_from_file_location("paa_model", PAA_MODEL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def analyze(scan_id: int, temperature_c: float) -> dict:
    if not ANALYTICS_URL:
        return _local_model().predict(temperature_c)
    url = ANALYTICS_URL.rstrip("/")
    if not url.startswith("http"):
        url = f"http://{url}"
    r = httpx.post(f"{url}/analyze", json={"scan_id": scan_id, "temperature_c": temperature_c}, timeout=30)
    r.raise_for_status()
    return r.json()
