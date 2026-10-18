"""Cyrcadia-style Local Data Manager.

Patients sync readings from their own wearable (over Bluetooth, relayed by the patient portal); admins can
upload readings as CSV. Readings are stored in the patient DB, sent to the PAA analytics service, and the
results are stored in the Core Lab DB.
"""

import csv
import io
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from . import analytics_client
from .auth import (
    COOKIE,
    SESSION_TTL,
    authenticate,
    create_session,
    current_user,
    delete_session,
    require_admin,
    require_patient,
    seed_users,
)
from .db import AnalysisResult, Device, Patient, Scan, SessionLocal, User, init_db

log = logging.getLogger("data-manager")

STATIC_DIR = Path(__file__).parent / "static"
DEVICE_BUFFER_CSV = Path(
    os.environ.get("DEVICE_BUFFER_CSV", Path(__file__).resolve().parents[2] / "data" / "device_buffer.csv")
)
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true")
MAX_BATCH = 500


class Reading(BaseModel):
    """One reading as stored on the device. The device doesn't know the patient; its owner does."""

    session_id: str = Field(min_length=1, max_length=64)
    seq: int = Field(ge=0)
    recorded_at: datetime
    temperature_c: float = Field(ge=20, le=45)


class DeviceSyncRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    readings: list[Reading] = Field(max_length=MAX_BATCH)


class ClinicRow(Reading):
    """One row of an admin CSV upload: a reading plus the patient and device it came from."""

    patient_uid: str = Field(min_length=1, max_length=64)
    patch_size: int = Field(ge=1, le=6)
    device_id: str = Field(min_length=1, max_length=64)


class Ack(BaseModel):
    session_id: str
    seq: int


class DeviceSyncResponse(BaseModel):
    accepted: int
    duplicates: int
    acks: list[Ack]


class LoginRequest(BaseModel):
    username: str
    password: str
    role: Literal["admin", "patient"]


class ConnectDeviceRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    seed_users()
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


def ingest(device_id: str, patient_uid: str, readings: list[Reading]) -> tuple[DeviceSyncResponse, list[int]]:
    """Store new readings and ack every reading received, including duplicates, so the device can drop them."""
    new_ids: list[int] = []
    duplicates = 0
    seen: set[tuple[str, int]] = set()
    with SessionLocal() as db:
        for r in readings:
            key = (r.session_id, r.seq)
            exists = key in seen or db.scalar(
                select(Scan.id).where(Scan.device_id == device_id, Scan.session_id == r.session_id, Scan.seq == r.seq)
            )
            seen.add(key)
            if exists:
                duplicates += 1
                continue
            scan = Scan(
                patient_uid=patient_uid,
                device_id=device_id,
                session_id=r.session_id,
                seq=r.seq,
                recorded_at=r.recorded_at,
                temperature_c=r.temperature_c,
            )
            db.add(scan)
            db.flush()
            new_ids.append(scan.id)
        db.commit()
    acks = [Ack(session_id=r.session_id, seq=r.seq) for r in readings]
    return DeviceSyncResponse(accepted=len(new_ids), duplicates=duplicates, acks=acks), new_ids


def parse_csv(text: str, model: type[BaseModel], required: set[str]) -> list:
    reader = csv.DictReader(io.StringIO(text))
    missing = required - set(reader.fieldnames or [])
    if missing:
        raise HTTPException(400, f"CSV missing columns: {', '.join(sorted(missing))}")
    rows = []
    for i, row in enumerate(reader, start=2):
        try:
            rows.append(model.model_validate(row))
        except ValidationError as e:
            err = e.errors()[0]
            raise HTTPException(400, f"Invalid row {i}: {err['loc'][0]} {err['msg']}") from e
    return rows


class _BufferRow(Reading):
    device_id: str


def _device_owner(db, device_id: str) -> str | None:
    device = db.get(Device, device_id)
    return device.patient_uid if device else None


# --- auth -------------------------------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(req: LoginRequest, response: Response) -> dict:
    """Separate logins: the patient portal only accepts patients, the admin dashboard only admins."""
    user = authenticate(req.username, req.password)
    if user is None or user.role != req.role:
        raise HTTPException(401, "Invalid username or password")
    response.set_cookie(
        COOKIE,
        create_session(user.id),
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return {"username": user.username, "role": user.role}


@app.post("/api/auth/logout")
def logout(response: Response, mirra_session: str | None = Cookie(default=None)) -> dict:
    if mirra_session:
        delete_session(mirra_session)
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(user: User = Depends(current_user)) -> dict:
    device_id = None
    if user.patient_uid:
        with SessionLocal() as db:
            device_id = db.scalar(select(Device.device_id).where(Device.patient_uid == user.patient_uid))
    return {"username": user.username, "role": user.role, "patient_uid": user.patient_uid, "device_id": device_id}


# --- patient portal ---------------------------------------------------------------------------------------


@app.post("/api/me/device")
def connect_device(req: ConnectDeviceRequest, user: User = Depends(require_patient)) -> dict:
    """Link a device to the logged-in patient (one device per patient). Its readings will be theirs."""
    with SessionLocal() as db:
        owner = _device_owner(db, req.device_id)
        if owner and owner != user.patient_uid:
            raise HTTPException(409, "This device is connected to another patient")
        for d in db.scalars(select(Device).where(Device.patient_uid == user.patient_uid)):
            if d.device_id != req.device_id:
                db.delete(d)
        if owner is None:
            db.add(Device(device_id=req.device_id, patient_uid=user.patient_uid))
        db.commit()
    return {"device_id": req.device_id}


@app.get("/api/me/scans")
def my_scans(user: User = Depends(require_patient)) -> list[dict]:
    with SessionLocal() as db:
        stmt = (
            select(Scan)
            .options(joinedload(Scan.patient), joinedload(Scan.result))
            .where(Scan.patient_uid == user.patient_uid)
            .order_by(Scan.recorded_at.desc())
        )
        return [_serialize(s) for s in db.scalars(stmt).unique()]


@app.post("/api/device/sync", response_model=DeviceSyncResponse)
def device_sync(
    req: DeviceSyncRequest, background: BackgroundTasks, user: User = Depends(require_patient)
) -> DeviceSyncResponse:
    """Receive a chunk of readings from the patient's device. Idempotent on (device_id, session_id, seq)."""
    with SessionLocal() as db:
        if _device_owner(db, req.device_id) != user.patient_uid:
            raise HTTPException(403, "Connect this device to your account first")
    resp, new_ids = ingest(req.device_id, user.patient_uid, req.readings)
    background.add_task(run_analysis, new_ids)
    return resp


@app.get("/api/simulated-device/buffer")
def simulated_device_buffer(_: User = Depends(require_patient)) -> dict:
    """Readings stored on the simulated ESP32 (data/device_buffer.csv). A real device replaces this."""
    if not DEVICE_BUFFER_CSV.exists():
        raise HTTPException(500, f"Device buffer not found at {DEVICE_BUFFER_CSV}")
    rows = parse_csv(DEVICE_BUFFER_CSV.read_text(), _BufferRow, set(_BufferRow.model_fields))
    device_ids = {r.device_id for r in rows}
    if len(device_ids) != 1:
        raise HTTPException(500, "Simulated device buffer must contain exactly one device_id")
    readings = [Reading.model_validate(r.model_dump(exclude={"device_id"})) for r in rows]
    return DeviceSyncRequest(device_id=device_ids.pop(), readings=readings).model_dump(mode="json")


# --- admin dashboard --------------------------------------------------------------------------------------


@app.post("/api/scans/upload", response_model=DeviceSyncResponse)
async def upload_scans(
    file: UploadFile, background: BackgroundTasks, _: User = Depends(require_admin)
) -> DeviceSyncResponse:
    rows: list[ClinicRow] = parse_csv((await file.read()).decode("utf-8-sig"), ClinicRow, set(ClinicRow.model_fields))
    groups: dict[tuple[str, str], list[Reading]] = {}
    with SessionLocal() as db:
        for row in rows:
            if db.get(Patient, row.patient_uid) is None:
                db.add(Patient(uid=row.patient_uid, patch_size=row.patch_size))
                db.flush()
            groups.setdefault((row.device_id, row.patient_uid), []).append(row)
        db.commit()
    accepted = duplicates = 0
    acks: list[Ack] = []
    for (device_id, patient_uid), readings in groups.items():
        resp, new_ids = ingest(device_id, patient_uid, readings)
        background.add_task(run_analysis, new_ids)
        accepted += resp.accepted
        duplicates += resp.duplicates
        acks += resp.acks
    return DeviceSyncResponse(accepted=accepted, duplicates=duplicates, acks=acks)


@app.post("/api/scans/{scan_id}/analyze")
def reanalyze(scan_id: int, background: BackgroundTasks, _: User = Depends(require_admin)) -> dict:
    with SessionLocal() as db:
        if db.get(Scan, scan_id) is None:
            raise HTTPException(404, "Scan not found")
    background.add_task(run_analysis, [scan_id])
    return {"queued": scan_id}


@app.get("/api/summary")
def summary(_: User = Depends(require_admin)) -> dict:
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


@app.get("/api/scans")
def list_scans(status: str | None = None, q: str | None = None, _: User = Depends(require_admin)) -> list[dict]:
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
def get_scan(scan_id: int, _: User = Depends(require_admin)) -> dict:
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id, options=[joinedload(Scan.patient), joinedload(Scan.result)])
        if scan is None:
            raise HTTPException(404, "Scan not found")
        return _serialize(scan)


@app.get("/api/patients")
def list_patients(_: User = Depends(require_admin)) -> list[dict]:
    with SessionLocal() as db:
        rows = db.execute(
            select(Patient, func.count(Scan.id)).outerjoin(Scan).group_by(Patient.uid).order_by(Patient.uid)
        ).all()
        devices = dict(db.execute(select(Device.patient_uid, Device.device_id)).all())
        return [
            {
                "uid": p.uid,
                "patch_size": p.patch_size,
                "registered_at": p.registered_at.isoformat(),
                "device_id": devices.get(p.uid),
                "scans": n,
            }
            for p, n in rows
        ]


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def patient_portal() -> FileResponse:
    return FileResponse(STATIC_DIR / "patient.html")


@app.get("/admin")
def admin_dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html")
