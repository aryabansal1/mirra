"""Cyrcadia-style Local Data Manager: receives scan data from the website,
stores it in the patient DB, sends it to the PAA analytics service, and stores
the results in the Core Lab DB."""

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
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from . import analytics_client
from .db import AnalysisResult, Patient, Scan, SessionLocal, init_db

log = logging.getLogger("data-manager")

STATIC_DIR = Path(__file__).parent / "static"
SYNC_CSV_PATH = Path(
    os.environ.get("SYNC_CSV_PATH", Path(__file__).resolve().parents[2] / "data" / "synthetic_scans.csv")
)
REQUIRED_COLUMNS = {"patient_uid", "recorder_id", "patch_size", "scan_start", "temperature_c"}


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
        "recorder_id": scan.recorder_id,
        "scan_start": scan.scan_start.isoformat(),
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


def ingest_csv(text: str) -> tuple[list[int], int]:
    reader = csv.DictReader(io.StringIO(text))
    missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
    if missing:
        raise HTTPException(400, f"CSV missing columns: {', '.join(sorted(missing))}")
    new_ids: list[int] = []
    skipped = 0
    with SessionLocal() as db:
        for row in reader:
            try:
                uid = row["patient_uid"].strip()
                recorder_id = row["recorder_id"].strip()
                patch_size = int(row["patch_size"])
                scan_start = datetime.fromisoformat(row["scan_start"].strip())
                temperature_c = float(row["temperature_c"])
            except (ValueError, AttributeError) as e:
                raise HTTPException(400, f"Invalid row {row}: {e}") from e
            patient = db.get(Patient, uid)
            if patient is None:
                patient = Patient(uid=uid, patch_size=patch_size)
                db.add(patient)
                db.flush()
            dup = db.scalar(
                select(Scan).where(
                    Scan.patient_uid == uid, Scan.recorder_id == recorder_id, Scan.scan_start == scan_start
                )
            )
            if dup:
                skipped += 1
                continue
            scan = Scan(patient_uid=uid, recorder_id=recorder_id, scan_start=scan_start, temperature_c=temperature_c)
            db.add(scan)
            db.flush()
            new_ids.append(scan.id)
        db.commit()
    return new_ids, skipped


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/scans/sync")
def sync_scans(background: BackgroundTasks) -> dict:
    """Simulates syncing scans already transferred from the recorder over Bluetooth."""
    if not SYNC_CSV_PATH.exists():
        raise HTTPException(500, f"Sync CSV not found at {SYNC_CSV_PATH}")
    ids, skipped = ingest_csv(SYNC_CSV_PATH.read_text())
    background.add_task(run_analysis, ids)
    return {"imported": len(ids), "skipped_duplicates": skipped}


@app.post("/api/scans/upload")
async def upload_scans(file: UploadFile, background: BackgroundTasks) -> dict:
    ids, skipped = ingest_csv((await file.read()).decode("utf-8-sig"))
    background.add_task(run_analysis, ids)
    return {"imported": len(ids), "skipped_duplicates": skipped}


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


@app.get("/api/scans")
def list_scans(status: str | None = None, q: str | None = None) -> list[dict]:
    with SessionLocal() as db:
        stmt = select(Scan).options(joinedload(Scan.patient), joinedload(Scan.result)).order_by(Scan.scan_start.desc())
        if status:
            stmt = stmt.where(Scan.status == status)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Scan.patient_uid.ilike(like), Scan.recorder_id.ilike(like)))
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
