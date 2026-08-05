from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.security import require_bearer_token
from app.memory.database import get_db_session
from app.memory.repository import create_memory, delete_memory, search_memories
from app.models.schemas import MemoryCreateRequest, MemoryResponse, MemorySearchResponse

router = APIRouter()


@router.post("/v1/memories", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
def post_memory(
    payload: MemoryCreateRequest,
    _token: str = Depends(require_bearer_token),
    db: Session = Depends(get_db_session),
) -> MemoryResponse:
    return create_memory(db, payload)


@router.get("/v1/memories/search", response_model=MemorySearchResponse)
def get_memories_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=50),
    _token: str = Depends(require_bearer_token),
    db: Session = Depends(get_db_session),
) -> MemorySearchResponse:
    return MemorySearchResponse(results=search_memories(db, q, limit))


@router.delete("/v1/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory_endpoint(
    memory_id: str,
    _token: str = Depends(require_bearer_token),
    db: Session = Depends(get_db_session),
) -> None:
    deleted = delete_memory(db, memory_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
