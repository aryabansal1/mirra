import os

import httpx

ANALYTICS_URL = os.environ.get("ANALYTICS_URL", "http://localhost:8001")


def analyze(scan_id: int, temperature_c: float) -> dict:
    url = ANALYTICS_URL.rstrip("/")
    if not url.startswith("http"):
        url = f"http://{url}"
    r = httpx.post(f"{url}/analyze", json={"scan_id": scan_id, "temperature_c": temperature_c}, timeout=30)
    r.raise_for_status()
    return r.json()
