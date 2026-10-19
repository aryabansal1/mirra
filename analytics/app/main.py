from fastapi import FastAPI
from pydantic import BaseModel

from .model import predict

app = FastAPI(title="Mirra PAA Analytics")


class AnalyzeRequest(BaseModel):
    scan_id: int | None = None
    temperature_c: float


class AnalyzeResponse(BaseModel):
    scan_id: int | None
    abnormal: bool
    label: str
    threshold_c: float
    model_version: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    return AnalyzeResponse(scan_id=req.scan_id, **predict(req.temperature_c))
