"""Cyrcadia-style Local Data Manager.

Receives readings synced from the wearable device (over Bluetooth, relayed by the website), stores them
in the patient DB, sends them to the PAA analytics service, and stores the results in the Core Lab DB.
"""

import csv
import io
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from . import analytics_client
from .db import AnalysisResult, Patient, Scan, SessionLocal, init_db

log = logging.getLogger("data-manager")

STATIC_DIR = Path(__file__).parent / "static"
DEVICE_BUFFER_CSV = Path(
    os.environ.get("DEVICE_BUFFER_CSV", Path(__file__).resolve().parents[2] / "data" / "device_buffer.csv")
)
MAX_BATCH = 500


class Reading(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    seq: int = Field(ge=0)
    recorded_at: datetime
    patient_uid: str = Field(min_length=1, max_length=64)
    patch_size: int = Field(ge=1, le=6)
    temperature_c: float = Field(ge=20, le=45)


class DeviceSyncRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    readings: list[Reading] = Field(max_length=MAX_BATCH)


class Ack(BaseModel):
    session_id: str
    seq: int


class DeviceSyncResponse(BaseModel):
    accepted: int
    duplicates: int
    acks: list[Ack]


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Mirra Data Manager", lifespan=lifespan)


def _serialize(scan: Scan) -> dict:
    r = scan.result
    return {
        "id": scan.id,
        "patient_uid": scan.patient_uid,
        "patch_size": scan.patient.patch_size,
        "device_id": scan.device_id,
        "session_id": scan.session_id,
        "seq": scan.seq,
        "recorded_at": scan.recorded_at.isoformat(),
        "temperature_c": scan.temperature_c,
        "status": scan.status,
        "uploaded_at": scan.created_at.isoformat() if scan.created_at else None,
        "result": None
        if r is None
        else {
            "abnormal": r.abnormal,
            "label": r.label,
            "threshold_c": r.threshold_c,
            "model_version": r.model_version,
            "analyzed_at": r.analyzed_at.isoformat() if r.analyzed_at else None,
        },
    }


def run_analysis(scan_ids: list[int]) -> None:
    for scan_id in scan_ids:
        with SessionLocal() as db:
            scan = db.get(Scan, scan_id)
            if scan is None:
                continue
            scan.status = "analyzing"
            db.commit()
            try:
                out = analytics_client.analyze(scan.id, scan.temperature_c)
            except Exception:
                log.exception("analysis failed for scan %s", scan_id)
                scan.status = "failed"
                db.commit()
                continue
            existing = db.scalar(select(AnalysisResult).where(AnalysisResult.scan_id == scan.id))
            if existing:
                db.delete(existing)
                db.flush()
            db.add(
                AnalysisResult(
                    scan_id=scan.id,
                    abnormal=out["abnormal"],
                    label=out["label"],
                    threshold_c=out["threshold_c"],
                    model_version=out["model_version"],
                )
            )
            scan.status = "results_ready"
            db.commit()


def ingest(req: DeviceSyncRequest) -> tuple[DeviceSyncResponse, list[int]]:
    """Store new readings and ack every reading received, including duplicates, so the device can drop them."""
    new_ids: list[int] = []
    duplicates = 0
    seen: set[tuple[str, int]] = set()
    with SessionLocal() as db:
        for r in req.readings:
            key = (r.session_id, r.seq)
            exists = key in seen or db.scalar(
                select(Scan.id).where(
                    Scan.device_id == req.device_id, Scan.session_id == r.session_id, Scan.seq == r.seq
                )
            )
            seen.add(key)
            if exists:
                duplicates += 1
                continue
            if db.get(Patient, r.patient_uid) is None:
                db.add(Patient(uid=r.patient_uid, patch_size=r.patch_size))
                db.flush()
            scan = Scan(
                patient_uid=r.patient_uid,
                device_id=req.device_id,
                session_id=r.session_id,
                seq=r.seq,
                recorded_at=r.recorded_at,
                temperature_c=r.temperature_c,
            )
            db.add(scan)
            db.flush()
            new_ids.append(scan.id)
        db.commit()
    acks = [Ack(session_id=r.session_id, seq=r.seq) for r in req.readings]
    return DeviceSyncResponse(accepted=len(new_ids), duplicates=duplicates, acks=acks), new_ids


def parse_device_csv(text: str) -> list[DeviceSyncRequest]:
    reader = csv.DictReader(io.StringIO(text))
    required = {"device_id", *Reading.model_fields}
    missing = required - set(reader.fieldnames or [])
    if missing:
        raise HTTPException(400, f"CSV missing columns: {', '.join(sorted(missing))}")
    by_device: dict[str, list[Reading]] = {}
    for i, row in enumerate(reader, start=2):
        try:
            by_device.setdefault(row["device_id"].strip(), []).append(Reading.model_validate(row))
        except ValidationError as e:
            raise HTTPException(400, f"Invalid row {i}: {e.errors()[0]['loc'][0]} {e.errors()[0]['msg']}") from e
    return [DeviceSyncRequest(device_id=d, readings=rs) for d, rs in by_device.items()]


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/device/sync", response_model=DeviceSyncResponse)
def device_sync(req: DeviceSyncRequest, background: BackgroundTasks) -> DeviceSyncResponse:
    """Receive a chunk of readings from a device. Idempotent on (device_id, session_id, seq)."""
    resp, new_ids = ingest(req)
    background.add_task(run_analysis, new_ids)
    return resp


@app.get("/api/simulated-device/buffer")
def simulated_device_buffer() -> dict:
    """Readings stored on the simulated ESP32 (data/device_buffer.csv). A real device replaces this."""
    if not DEVICE_BUFFER_CSV.exists():
        raise HTTPException(500, f"Device buffer not found at {DEVICE_BUFFER_CSV}")
    reqs = parse_device_csv(DEVICE_BUFFER_CSV.read_text())
    if len(reqs) != 1:
        raise HTTPException(500, "Simulated device buffer must contain exactly one device_id")
    return reqs[0].model_dump(mode="json")


@app.post("/api/scans/upload", response_model=DeviceSyncResponse)
async def upload_scans(file: UploadFile, background: BackgroundTasks) -> DeviceSyncResponse:
    accepted = duplicates = 0
    acks: list[Ack] = []
    for req in parse_device_csv((await file.read()).decode("utf-8-sig")):
        resp, new_ids = ingest(req)
        background.add_task(run_analysis, new_ids)
        accepted += resp.accepted
        duplicates += resp.duplicates
        acks += resp.acks
    return DeviceSyncResponse(accepted=accepted, duplicates=duplicates, acks=acks)


@app.post("/api/scans/{scan_id}/analyze")
def reanalyze(scan_id: int, background: BackgroundTasks) -> dict:
    with SessionLocal() as db:
        if db.get(Scan, scan_id) is None:
            raise HTTPException(404, "Scan not found")
    background.add_task(run_analysis, [scan_id])
    return {"queued": scan_id}


@app.get("/api/summary")
def summary() -> dict:
    with SessionLocal() as db:
        by_status = dict(db.execute(select(Scan.status, func.count()).group_by(Scan.status)).all())
        abnormal = db.scalar(select(func.count()).select_from(AnalysisResult).where(AnalysisResult.abnormal)) or 0
        patients = db.scalar(select(func.count()).select_from(Patient)) or 0
    return {
        "patients": patients,
        "uploaded": by_status.get("uploaded", 0),
        "analyzing": by_status.get("analyzing", 0),
        "results_ready": by_status.get("results_ready", 0),
        "failed": by_status.get("failed", 0),
        "abnormal": abnormal,
    }


@app.get("/api/devices/{device_id}")
def device_status(device_id: str) -> dict:
    with SessionLocal() as db:
        count, last_seq, last_sync = db.execute(
            select(func.count(Scan.id), func.max(Scan.seq), func.max(Scan.created_at)).where(
                Scan.device_id == device_id
            )
        ).one()
    return {
        "device_id": device_id,
        "readings_received": count,
        "last_seq": last_seq,
        "last_synced_at": last_sync.isoformat() if last_sync else None,
    }


@app.get("/api/scans")
def list_scans(status: str | None = None, q: str | None = None) -> list[dict]:
    with SessionLocal() as db:
        stmt = select(Scan).options(joinedload(Scan.patient), joinedload(Scan.result)).order_by(Scan.recorded_at.desc())
        if status:
            stmt = stmt.where(Scan.status == status)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(Scan.patient_uid.ilike(like), Scan.device_id.ilike(like), Scan.session_id.ilike(like))
            )
        return [_serialize(s) for s in db.scalars(stmt).unique()]


@app.get("/api/scans/{scan_id}")
def get_scan(scan_id: int) -> dict:
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id, options=[joinedload(Scan.patient), joinedload(Scan.result)])
        if scan is None:
            raise HTTPException(404, "Scan not found")
        return _serialize(scan)


@app.get("/api/patients")
def list_patients() -> list[dict]:
    with SessionLocal() as db:
        rows = db.execute(
            select(Patient, func.count(Scan.id)).outerjoin(Scan).group_by(Patient.uid).order_by(Patient.uid)
        ).all()
        return [
            {"uid": p.uid, "patch_size": p.patch_size, "registered_at": p.registered_at.isoformat(), "scans": n}
            for p, n in rows
        ]


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
