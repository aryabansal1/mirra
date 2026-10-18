"""Predictive Analytic Analysis (PAA) model.

Placeholder threshold model. To swap in a real ML model, replace `predict`
(and bump MODEL_VERSION) while keeping the same input/output shape.
"""

THRESHOLD_C = 35.0
MODEL_VERSION = "threshold-v1"


def predict(temperature_c: float) -> dict:
    abnormal = temperature_c > THRESHOLD_C
    return {
        "abnormal": abnormal,
        "label": "Abnormal - suspected malignancy" if abnormal else "Normal",
        "threshold_c": THRESHOLD_C,
        "model_version": MODEL_VERSION,
    }
