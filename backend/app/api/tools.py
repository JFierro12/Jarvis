from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.security import require_bearer_token
from app.memory.database import get_db_session
from app.memory.models import MemoryRow
from app.memory.repository import delete_memory
from app.models.schemas import (
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolProposeRequest,
    ToolProposeResponse,
)
from app.tools.policy import evaluate_tool_call

router = APIRouter()


@router.post("/v1/tools/propose", response_model=ToolProposeResponse)
def propose_tool(payload: ToolProposeRequest, _token: str = Depends(require_bearer_token)) -> ToolProposeResponse:
    result = evaluate_tool_call(payload.tool_name, payload.target, payload.granted_permissions)
    return ToolProposeResponse(
        decision=result.decision,
        reason=result.reason,
        confirmation_summary=result.confirmation_summary,
        risk_level=result.risk_level,
    )


@router.post("/v1/tools/execute", response_model=ToolExecuteResponse)
def execute_tool(
    payload: ToolExecuteRequest,
    _token: str = Depends(require_bearer_token),
    db: Session = Depends(get_db_session),
) -> ToolExecuteResponse:
    # Re-run policy at execution time too — a client is never trusted to
    # have enforced this itself, and a stale "allow" from `propose` is not
    # sufficient authorization on its own.
    result = evaluate_tool_call(payload.tool_name, payload.target, granted_permissions=[])
    if result.decision == "deny":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result.reason)
    if result.decision == "require_confirmation" and not payload.confirmed:
        raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail="Confirmation required")

    if payload.tool_name == "get_current_time":
        return ToolExecuteResponse(success=True, output=datetime.now().strftime("%H:%M"))

    if payload.tool_name == "delete_memory":
        deleted = delete_memory(db, payload.target)
        # Idempotent: deleting an already-deleted (or nonexistent) memory is
        # reported plainly rather than as an error — retries are always safe.
        return ToolExecuteResponse(success=True, output="deleted" if deleted else "already deleted")

    if payload.tool_name == "delete_all_memories":
        db.execute(update(MemoryRow).where(MemoryRow.deleted_at.is_(None)).values(deleted_at=datetime.utcnow()))
        db.commit()
        return ToolExecuteResponse(success=True, output="deleted all")

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"No server-side execution implemented for {payload.tool_name}",
    )
