import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryRow(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    original_user_text: Mapped[str] = mapped_column(Text)
    normalized_summary: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    coarse_location_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    object_labels_csv: Mapped[str] = mapped_column(Text, default="")
    tags_csv: Mapped[str] = mapped_column(Text, default="")
    retention_policy: Mapped[str] = mapped_column(String(32), default="days365")
    sensitivity: Mapped[str] = mapped_column(String(16), default="normal")
    created_by: Mapped[str] = mapped_column(String(64), default="user")
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
