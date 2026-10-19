import os
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///./mirra.db")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


engine = create_engine(_database_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# Local Patient Database
class Patient(Base):
    __tablename__ = "patients"

    uid: Mapped[str] = mapped_column(String(64), primary_key=True)
    patch_size: Mapped[int] = mapped_column(Integer)
    registered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    scans: Mapped[list["Scan"]] = relationship(back_populates="patient")


class Scan(Base):
    """One synced reading. (device_id, session_id, seq) identifies it on the device and makes sync idempotent."""

    __tablename__ = "scans"
    __table_args__ = (UniqueConstraint("device_id", "session_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_uid: Mapped[str] = mapped_column(ForeignKey("patients.uid"))
    device_id: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    temperature_c: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    patient: Mapped[Patient] = relationship(back_populates="scans")
    result: Mapped["AnalysisResult | None"] = relationship(back_populates="scan", uselist=False)


# Core Lab Database
class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), unique=True)
    abnormal: Mapped[bool] = mapped_column(Boolean)
    label: Mapped[str] = mapped_column(String(128))
    threshold_c: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(64))
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    scan: Mapped[Scan] = relationship(back_populates="result")


# Accounts, login sessions and patient-owned devices
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))  # "admin" | "patient"
    patient_uid: Mapped[str | None] = mapped_column(ForeignKey("patients.uid"), nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    user: Mapped[User] = relationship()


class Device(Base):
    """A wearable linked to the patient whose account connected it. Its readings belong to that patient."""

    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_uid: Mapped[str] = mapped_column(ForeignKey("patients.uid"))
    connected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


def init_db() -> None:
    Base.metadata.create_all(engine)
