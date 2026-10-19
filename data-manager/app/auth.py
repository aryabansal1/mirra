"""Cookie-based login for the admin dashboard and the patient portal."""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy import select

from .db import AuthSession, Patient, SessionLocal, User

COOKIE = "mirra_session"
SESSION_TTL = timedelta(days=7)
PBKDF2_ITERATIONS = 200_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, dk_hex = stored.split("$", 1)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk.hex(), dk_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(username: str, password: str) -> User | None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if user is None or not verify_password(password, user.password_hash):
            return None
        return user


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with SessionLocal() as db:
        db.add(AuthSession(token_hash=_token_hash(token), user_id=user_id, expires_at=_utcnow() + SESSION_TTL))
        db.commit()
    return token


def delete_session(token: str) -> None:
    with SessionLocal() as db:
        session = db.get(AuthSession, _token_hash(token))
        if session:
            db.delete(session)
            db.commit()


def current_user(mirra_session: str | None = Cookie(default=None)) -> User:
    if mirra_session:
        with SessionLocal() as db:
            session = db.get(AuthSession, _token_hash(mirra_session))
            if session and session.expires_at > _utcnow():
                return session.user
    raise HTTPException(401, "Not logged in")


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admins only")
    return user


def require_patient(user: User = Depends(current_user)) -> User:
    if user.role != "patient":
        raise HTTPException(403, "Patients only")
    return user


def seed_users() -> None:
    """Create the demo admin and patient accounts (credentials from env) if they don't exist yet."""
    admin = (os.environ.get("ADMIN_USERNAME", "admin"), os.environ.get("ADMIN_PASSWORD", "admin"))
    patient = (os.environ.get("DEMO_PATIENT_USERNAME", "patient"), os.environ.get("DEMO_PATIENT_PASSWORD", "patient"))
    patient_uid = os.environ.get("DEMO_PATIENT_UID", "0852-032-001")
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == admin[0])) is None:
            db.add(User(username=admin[0], password_hash=hash_password(admin[1]), role="admin"))
        if db.scalar(select(User).where(User.username == patient[0])) is None:
            if db.get(Patient, patient_uid) is None:
                db.add(Patient(uid=patient_uid, patch_size=2))
                db.flush()
            db.add(
                User(
                    username=patient[0],
                    password_hash=hash_password(patient[1]),
                    role="patient",
                    patient_uid=patient_uid,
                )
            )
        db.commit()
