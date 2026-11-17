import base64
import hashlib
import json
from datetime import datetime

import psutil
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.core.security import caller_identity, require_approval_credential, require_bearer_token
from app.memory.database import get_db_session
from app.memory.models import MemoryRow
from app.memory.repository import delete_memory
from app.models.schemas import (
    ActionApprovalResponse,
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
from app.tools.pending_actions import PendingActionError, get_pending_action_store, normalize_action
from app.tools.policy import evaluate_tool_call

router = APIRouter()


def policy_revision(settings):
    return hashlib.sha256(json.dumps([settings.policy_revision, sorted(settings.granted_permission_set),
                                     settings.allowed_folders], sort_keys=True).encode()).hexdigest()


def owned_action(action_id, request, token, settings):
    identity = caller_identity(request, token, settings)
    record = get_pending_action_store().get(action_id)
    if record is None:
        raise HTTPException(status_code=412, detail="Unknown action")
    action = record.action
    if (action.principal, action.session_id, action.origin_device) != (
        identity.principal, identity.session_id, identity.device_id
    ):
        raise HTTPException(status_code=403, detail="Action belongs to another session")
    if action.policy_revision != policy_revision(settings):
        raise HTTPException(status_code=412, detail="Policy changed; propose again")
    return record, identity


# Mirrors ios/JarvisKit/Sources/JarvisKit/Mocks/MockSmartHomeClient.swift's
# allowlist — no real Home Assistant integration on the backend yet (see
# ios/JarvisKit/Sources/JarvisKit/Integrations/HomeAssistantSmartHomeClient.swift
# for what a real one looks like), so this keeps desktop parity with the
# same demo-mode entity the phone app's mock exposes.
_SMART_HOME_ENTITIES = {"light.bedroom": {"friendly_name": "Bedroom Light", "state": "on"}}


@router.post("/v1/tools/propose", response_model=ToolProposeResponse)
def propose_tool(
    payload: ToolProposeRequest,
    request: Request,
    token: str = Depends(require_bearer_token),
    settings: Settings = Depends(get_settings),
) -> ToolProposeResponse:
    # Permissions come from this installation's server-side grant, never
    # from the request body.
    identity = caller_identity(request, token, settings)
    grants = identity.grants & settings.granted_permission_set
    tool_name = payload.tool_name.strip().lower()
    # No free-form argument channel in this version. File content is separate.
    if payload.arguments or (payload.content and tool_name not in {"write_file", "append_file"}):
        raise HTTPException(status_code=422, detail="Unsupported tool arguments or content")
    if len(payload.target) > 4096 or len(payload.content) > 65536:
        raise HTTPException(status_code=422, detail="Action exceeds size limit")
    result = evaluate_tool_call(tool_name, payload.target, sorted(grants))
    if settings.live_mode:
        from app.api.runtime import capabilities_for
        available = {c.name for c in capabilities_for(request, identity, settings, payload.target_device or "desktop")}
        if tool_name not in available:
            return ToolProposeResponse(decision="deny", reason="Capability unavailable or permission missing")

    action_id = None
    expires_in = None
    if result.decision != "deny":
        # Normalized once here; this stored object is what gets approved
        # and later executed. Proposal yields an id to *ask about*, never
        # anything that authorizes on its own.
        action = normalize_action(
            tool_name=tool_name,
            target=payload.target,
            content=payload.content,
            arguments=payload.arguments,
            principal=identity.principal,
            session_id=identity.session_id,
            origin_device=identity.device_id,
            target_device=payload.target_device or "desktop",
            policy_revision=policy_revision(settings),
        )
        try:
            record = get_pending_action_store().propose(action)
        except PendingActionError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        if result.decision == "allow":
            get_pending_action_store().approve(record.action_id, approved_by="policy:auto")
        action_id = record.action_id
        expires_in = int(max(0.0, record.expires_at - record.created_at))

    return ToolProposeResponse(
        decision=result.decision,
        reason=result.reason,
        confirmation_summary=result.confirmation_summary,
        risk_level=result.risk_level,
        action_id=action_id,
        confirmation_expires_in_seconds=expires_in,
    )


@router.post("/v1/actions/{action_id}/approve", response_model=ActionApprovalResponse)
def approve_action(
    action_id: str,
    request: Request,
    _token: str = Depends(require_bearer_token),
    approver: str = Depends(require_approval_credential),
    settings: Settings = Depends(get_settings),
) -> ActionApprovalResponse:
    """Human-approval path, gated by a credential the API bearer token
    does not carry (see require_approval_credential for exactly what that
    does and does not prove)."""
    owned_action(action_id, request, _token, settings)
    try:
        record = get_pending_action_store().approve(action_id, approved_by="trusted-ui")
    except PendingActionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ActionApprovalResponse(action_id=record.action_id, state=record.state.value)


@router.post("/v1/actions/{action_id}/reject", response_model=ActionApprovalResponse)
def reject_action(
    action_id: str,
    request: Request,
    _token: str = Depends(require_bearer_token),
    approver: str = Depends(require_approval_credential),
    settings: Settings = Depends(get_settings),
) -> ActionApprovalResponse:
    owned_action(action_id, request, _token, settings)
    try:
        record = get_pending_action_store().reject(action_id)
    except PendingActionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ActionApprovalResponse(action_id=record.action_id, state=record.state.value)


@router.post("/v1/tools/execute", response_model=ToolExecuteResponse)
async def execute_tool(
    payload: ToolExecuteRequest,
    request: Request,
    token: str = Depends(require_bearer_token),
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ToolExecuteResponse:
    # Re-run policy at execution time too — a client is never trusted to
    # have enforced this itself, and a stale "allow" from `propose` is not
    # sufficient authorization on its own. Permissions are read from the
    # server-side grant, not from anything the caller sent.
    identity = caller_identity(request, token, settings)
    principal = identity.principal
    store = get_pending_action_store()

    # A policy change invalidates approvals decided under the old rules.
    store.invalidate_for_policy_revision(policy_revision(settings))

    if payload.action_id:
        owned_action(payload.action_id, request, token, settings)
        # Approved path: everything executed comes from the stored
        # canonical action, so there is nothing for the client to
        # resubmit differently between approval and execution.
        try:
            record, already_executed = store.begin_execution(payload.action_id, principal)
        except PendingActionError as exc:
            raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail=str(exc)) from exc

        if already_executed:
            # Safe retry after an uncertain outcome: report the recorded
            # result instead of repeating the side effect.
            return ToolExecuteResponse(
                success=record.execution_success is True,
                output=record.execution_result or "Execution outcome not yet known; do not repeat the action.",
                execution_id=record.execution_id,
                state="completed" if record.execution_success is not None else "unknown",
            )

        action = record.action
        execution_id = record.execution_id
    else:
        if settings.live_mode:
            raise HTTPException(status_code=412, detail="Propose first and execute by action_id")
        # Unapproved path. Legal only for actions policy auto-allows —
        # there is no approval to forge — and policy is still rechecked
        # below before anything runs.
        action = normalize_action(
            tool_name=payload.tool_name,
            target=payload.target,
            content=payload.content,
            arguments=payload.arguments,
            principal=principal,
            policy_revision=policy_revision(settings),
        )
        execution_id = None

    # Re-run policy at execution time regardless of path: a client is
    # never trusted to have enforced it, and a stale "allow" from propose
    # is not authorization. Permissions come from the server-side grant.
    result = evaluate_tool_call(
        action.tool_name, action.target, granted_permissions=sorted(identity.grants & settings.granted_permission_set)
    )
    if result.decision == "deny":
        if payload.action_id:
            store.record_result(payload.action_id, "Permission revoked", False)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result.reason)
    if result.decision == "require_confirmation" and not payload.action_id:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="Approval required: propose this action, have it approved, then execute by action_id",
        )

    # Dispatch runs the stored action; the wrapper stamps the
    # execution id and records the outcome so a retry can report it
    # instead of repeating the side effect.
    try:
        if action.tool_name in {"get_pc_status", "launch_app"} and action.target_device:
            agent = request.app.state.agents.get(action.target_device)
            if not agent or not agent.available() or not request.app.state.runtime.device_active(action.target_device):
                response = ToolExecuteResponse(success=False, output="Paired PC unavailable; no action executed")
            else:
                def run_agent():
                    # Called at actual worker entry, after any threadpool queue.
                    current = caller_identity(request, token, settings)
                    store.invalidate_for_policy_revision(policy_revision(settings))
                    decision = evaluate_tool_call(
                        action.tool_name, action.target,
                        granted_permissions=sorted(current.grants & settings.granted_permission_set),
                    )
                    if (decision.decision == "deny" or not agent.available()
                            or not request.app.state.runtime.device_active(action.target_device)):
                        raise PendingActionError("Permission or device availability changed before dispatch")
                    store.mark_started(payload.action_id)
                    return agent.execute(action.tool_name, action.target)
                response = await run_in_threadpool(run_agent)
        elif settings.live_mode and action.tool_name not in {
            "get_current_time", "delete_memory", "delete_all_memories",
            # Both are genuinely served in-process by this backend, so unlike
            # the device-routed tools they are not "unconnected" in live mode.
            "get_weather", "get_local_news",
        }:
            response = ToolExecuteResponse(success=False, output="Capability is not connected to this runtime")
        else:
            if payload.action_id:
                store.mark_started(payload.action_id)
            response = await _dispatch_tool(payload, action, db, settings)
    except PendingActionError as exc:
        response = ToolExecuteResponse(success=False, output=str(exc))
    except Exception:
        # Outcome may be ambiguous; preserve the consumed record and never retry.
        if payload.action_id:
            store.record_result(payload.action_id, "Execution failed or outcome unknown; inspect before retrying", False)
        raise
    if execution_id is not None:
        response.execution_id = execution_id
        store.record_result(payload.action_id, response.output, response.success)
        request.app.state.runtime.record_action_result(payload.action_id, response.output, response.success)
    return response


@router.post("/v1/actions/{action_id}/cancel", response_model=ActionApprovalResponse)
def cancel_action(action_id: str, request: Request, token=Depends(require_bearer_token),
                  settings: Settings = Depends(get_settings)):
    owned_action(action_id, request, token, settings)
    try:
        record = get_pending_action_store().cancel(action_id)
    except PendingActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ActionApprovalResponse(action_id=action_id, state=record.state.value)


@router.get("/v1/actions/{action_id}")
def action_status(action_id: str, request: Request, token=Depends(require_bearer_token),
                  settings: Settings = Depends(get_settings)):
    record, _identity = owned_action(action_id, request, token, settings)
    return {"action_id": action_id, "action": record.action.as_dict(), "state": record.state.value,
            "execution_id": record.execution_id, "success": record.execution_success, "output": record.execution_result}


async def _dispatch_tool(
    payload: ToolExecuteRequest,
    action,
    db: Session,
    settings: Settings,
) -> ToolExecuteResponse:
    payload = payload.model_copy(
        update={
            "tool_name": action.tool_name,
            "target": action.target,
            "content": action.content,
            "arguments": dict(action.arguments),
        }
    )

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

    if payload.tool_name in ("get_weather", "get_local_news"):
        # `target` is the only model-controlled channel (arguments are
        # schema-locked to empty), so a named place arrives as free text here
        # and is resolved through the same resolver as any other request. It
        # is never written back as the saved default: asking about Denver
        # must not relocate the user to Denver.
        from app.services.location import LocationUnavailable, get_location_resolver

        resolver = get_location_resolver(settings)
        try:
            location = resolver.resolve(payload.target or "")
        except LocationUnavailable as exc:
            # An honest question, not a fabricated default city.
            return ToolExecuteResponse(success=False, output=str(exc))

        if payload.tool_name == "get_weather":
            from app.services.weather import (
                WeatherUnavailable,
                get_weather_service,
                summarize,
            )

            try:
                report = await run_in_threadpool(
                    get_weather_service(settings).report, location, 3
                )
            except WeatherUnavailable as exc:
                return ToolExecuteResponse(success=False, output=str(exc))
            return ToolExecuteResponse(success=True, output=summarize(report))

        from app.services.news import NewsUnavailable, get_news_service
        from app.services.news import summarize as summarize_news

        try:
            result = await run_in_threadpool(
                get_news_service(settings).fetch, location,
                int(getattr(settings, "news_max_items", 8)),
            )
        except NewsUnavailable as exc:
            return ToolExecuteResponse(success=False, output=str(exc))
        return ToolExecuteResponse(success=True, output=summarize_news(result))

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
