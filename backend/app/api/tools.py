import base64
from datetime import datetime

import psutil
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
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
from app.services.browser_automation import BrowserAutomationError, get_browser_automation_service
from app.services.file_operations import (
    FileOperationError,
    append_file,
    create_folder,
    delete_file,
    list_files,
    move_file,
    read_file,
    write_file,
)
from app.services.os_automation import (
    OSAutomationError,
    close_app,
    focus_app,
    launch_app,
    list_open_windows,
    take_screenshot_bytes,
)
from app.services.vision import VisionUnavailableError, get_vision_provider
from app.tools.policy import evaluate_tool_call

router = APIRouter()

# Mirrors ios/JarvisKit/Sources/JarvisKit/Mocks/MockSmartHomeClient.swift's
# allowlist — no real Home Assistant integration on the backend yet (see
# ios/JarvisKit/Sources/JarvisKit/Integrations/HomeAssistantSmartHomeClient.swift
# for what a real one looks like), so this keeps desktop parity with the
# same demo-mode entity the phone app's mock exposes.
_SMART_HOME_ENTITIES = {"light.bedroom": {"friendly_name": "Bedroom Light", "state": "on"}}


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
async def execute_tool(
    payload: ToolExecuteRequest,
    _token: str = Depends(require_bearer_token),
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ToolExecuteResponse:
    # Re-run policy at execution time too — a client is never trusted to
    # have enforced this itself, and a stale "allow" from `propose` is not
    # sufficient authorization on its own.
    result = evaluate_tool_call(payload.tool_name, payload.target, granted_permissions=payload.granted_permissions)
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

    if payload.tool_name == "get_pc_status":
        # Unlike the iOS app (which reaches a separate physical PC through
        # pc-agent over the network), this backend already runs on the PC in
        # question — real local stats via psutil rather than a mock number.
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory().percent
        return ToolExecuteResponse(success=True, output=f"CPU {cpu:.0f}%, RAM {ram:.0f}%")

    if payload.tool_name == "control_home_device":
        # A reasoning model can only ever propose {tool_name, target} — its
        # `arguments` are schema-locked to always be empty (see
        # REASON_JSON_SCHEMA in app/services/anthropic_reasoning.py) so it
        # has no channel to smuggle parameters past policy. `target` was
        # already fully model-controlled free text before this, so parsing
        # "<entity_id> on|off" out of it doesn't loosen that boundary —
        # it's still just an entity id string, only slightly richer.
        entity_id, _, state_word = payload.target.rpartition(" ")
        if state_word.lower() in ("on", "off"):
            state = state_word.lower()
        else:
            entity_id, state = payload.target, "on"
        entity = _SMART_HOME_ENTITIES.get(entity_id)
        if entity is None:
            return ToolExecuteResponse(success=False, output=f"{entity_id or '(no target)'} is not an allowlisted device")
        state = payload.arguments.get("state", state)
        entity["state"] = state
        return ToolExecuteResponse(success=True, output=f"{entity['friendly_name']} is now {state}")

    if payload.tool_name == "open_website":
        site_key, _, query = payload.target.strip().lower().partition(" ")
        try:
            output = await get_browser_automation_service().open_site(site_key, query.strip())
        except BrowserAutomationError as exc:
            return ToolExecuteResponse(success=False, output=str(exc))
        return ToolExecuteResponse(success=True, output=output)

    if payload.tool_name == "close_website":
        site_key = payload.target.strip().lower()
        output = await get_browser_automation_service().close_site(site_key)
        return ToolExecuteResponse(success=True, output=output)

    if payload.tool_name == "launch_app":
        try:
            output = await run_in_threadpool(launch_app, payload.target)
        except OSAutomationError as exc:
            return ToolExecuteResponse(success=False, output=str(exc))
        return ToolExecuteResponse(success=True, output=output)

    if payload.tool_name == "focus_app":
        try:
            output = await run_in_threadpool(focus_app, payload.target)
        except OSAutomationError as exc:
            return ToolExecuteResponse(success=False, output=str(exc))
        return ToolExecuteResponse(success=True, output=output)

    if payload.tool_name == "close_app":
        try:
            output = await run_in_threadpool(close_app, payload.target)
        except OSAutomationError as exc:
            return ToolExecuteResponse(success=False, output=str(exc))
        return ToolExecuteResponse(success=True, output=output)

    if payload.tool_name == "list_open_windows":
        windows = await run_in_threadpool(list_open_windows)
        output = ", ".join(windows) if windows else "No visible windows found."
        return ToolExecuteResponse(success=True, output=output)

    if payload.tool_name == "take_screenshot":
        try:
            image_bytes = await run_in_threadpool(take_screenshot_bytes)
        except OSAutomationError as exc:
            return ToolExecuteResponse(success=False, output=str(exc))
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        question = payload.target.strip() or "Describe what's currently visible on the screen."
        provider = get_vision_provider(settings.vision_provider)
        try:
            result = await run_in_threadpool(provider.analyze, image_b64, question)
        except VisionUnavailableError as exc:
            return ToolExecuteResponse(success=False, output=f"Screenshot captured but analysis failed: {exc}")
        return ToolExecuteResponse(success=True, output=result.answer)

    # ---- File operations (every path re-checked against the permitted
    # roots inside file_operations, never trusted from the request) ----
    file_tools = {
        "list_files": lambda: list_files(payload.target, settings.allowed_folders),
        "read_file": lambda: read_file(payload.target, settings.allowed_folders),
        "write_file": lambda: write_file(payload.target, payload.content, settings.allowed_folders),
        "append_file": lambda: append_file(payload.target, payload.content, settings.allowed_folders),
        "create_folder": lambda: create_folder(payload.target, settings.allowed_folders),
        "move_file": lambda: move_file(payload.target, settings.allowed_folders),
        "delete_file": lambda: delete_file(payload.target, settings.allowed_folders),
    }
    if payload.tool_name in file_tools:
        try:
            output = await run_in_threadpool(file_tools[payload.tool_name])
        except FileOperationError as exc:
            return ToolExecuteResponse(success=False, output=str(exc))
        return ToolExecuteResponse(success=True, output=output)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"No server-side execution implemented for {payload.tool_name}",
    )
