from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.memory.models import MemoryRow
from app.models.schemas import MemoryCreateRequest, MemoryResponse


def _to_response(row: MemoryRow) -> MemoryResponse:
    return MemoryResponse(
        id=row.id,
        type=row.type,
        title=row.title,
        original_user_text=row.original_user_text,
        normalized_summary=row.normalized_summary,
        timestamp=row.timestamp.isoformat(),
        latitude=row.latitude,
        longitude=row.longitude,
        coarse_location_name=row.coarse_location_name,
        extracted_text=row.extracted_text,
        object_labels=[t for t in row.object_labels_csv.split(",") if t],
        tags=[t for t in row.tags_csv.split(",") if t],
        retention_policy=row.retention_policy,
        sensitivity=row.sensitivity,
    )


def create_memory(db: Session, payload: MemoryCreateRequest) -> MemoryResponse:
    row = MemoryRow(
        type=payload.type,
        title=payload.title,
        original_user_text=payload.original_user_text,
        normalized_summary=payload.normalized_summary,
        latitude=payload.latitude,
        longitude=payload.longitude,
        coarse_location_name=payload.coarse_location_name,
        extracted_text=payload.extracted_text,
        object_labels_csv=",".join(payload.object_labels),
        tags_csv=",".join(payload.tags),
        retention_policy=payload.retention_policy,
        sensitivity=payload.sensitivity,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_response(row)


def search_memories(db: Session, query: str, limit: int = 5) -> List[MemoryResponse]:
    stmt = select(MemoryRow).where(MemoryRow.deleted_at.is_(None))
    rows = db.execute(stmt).scalars().all()

    needle_words = [w for w in query.lower().split() if w]
    scored = []
    for row in rows:
        haystack = f"{row.title} {row.normalized_summary} {row.tags_csv}".lower()
        matches = sum(1 for w in needle_words if w in haystack)
        if matches == 0:
            continue
        age_days = (datetime.now(timezone.utc) - row.timestamp.replace(tzinfo=timezone.utc)).days
        recency_boost = 1.0 / (1.0 + age_days)
        scored.append((matches + recency_boost, row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [_to_response(row) for _, row in scored[:limit]]


def delete_memory(db: Session, memory_id: str) -> bool:
    row = db.get(MemoryRow, memory_id)
    if row is None or row.deleted_at is not None:
        return False
    row.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return True


def get_memory(db: Session, memory_id: str) -> Optional[MemoryRow]:
    return db.get(MemoryRow, memory_id)
