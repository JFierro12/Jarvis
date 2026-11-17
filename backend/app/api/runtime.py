import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.core.security import caller_identity, require_approval_credential, require_bearer_token
from app.models.runtime import Capability, Enrollment, Invitation, SessionCredentials, TurnRequest, TurnResponse
from app.models.schemas import ContextItem, ToolProposeRequest
from app.services.anthropic_reasoning import SYSTEM_PROMPT, TUNED_SYSTEM_PROMPT
from app.services.reasoning import get_reasoning_provider
from app.tools.registry import TOOL_DEFINITIONS

router = APIRouter(prefix="/v2")
def prompt_version(settings: Settings) -> str:
    """Identity of the exact instruction text a conversation was held under.

    Derived from the prompt body rather than hand-maintained. The old constant
    was a literal string, so editing SYSTEM_PROMPT did not change it, and an
    existing conversation kept running under instructions it was never created
    with -- exactly the silent reuse the version check exists to prevent. Now
    any edit to either prompt, or a switch of local_llm_prompt_profile,
    produces a new id, and reusing an older conversation returns 409.
    """
    profile = (settings.local_llm_prompt_profile or "production").strip().lower()
    body = TUNED_SYSTEM_PROMPT if profile == "tuned" else SYSTEM_PROMPT
    if profile != "tuned":
        # Anything unrecognized falls back to the full prompt (see
        # LocalLanguageReasoningProvider), so it must not read as its own
        # profile here or a typo would look like a configuration change.
        profile = "production"
    return f"jarvis-v3-{profile}-{hashlib.sha256(body.encode()).hexdigest()[:16]}"
IMPLEMENTED = {"get_current_time", "get_pc_status", "launch_app", "delete_memory",
               "delete_all_memories", "get_weather", "get_local_news"}


def configured(name: str, settings) -> bool:
    """Whether a capability can actually produce a real answer right now.

    Being implemented is not the same as being usable. Advertising
    `get_local_news` with no feeds configured would have JARVIS claim a
    capability whose only possible output is an apology, and the project's
    behavioural rule is that it must not claim access to a source it does not
    have. So configuration is checked here, at discovery time, rather than
    discovered by the user at the point of failure.

    The mock providers are deliberately *not* advertised in live mode: a
    deterministic fake forecast is a development aid, not a weather service.
    """
    if name == "get_weather":
        provider = (getattr(settings, "weather_provider", "mock") or "mock").lower()
        return provider != "mock" if settings.live_mode else True
    if name == "get_local_news":
        provider = (getattr(settings, "news_provider", "mock") or "mock").lower()
        if settings.live_mode and provider == "mock":
            return False
        if provider == "rss":
            from app.services.news import parse_feed_specs
            return bool(parse_feed_specs(getattr(settings, "news_feeds", "")))
        return True
    return True


def capabilities_for(request, identity, settings, device):
    agent = request.app.state.agents.get(device)
    live = bool(agent and agent.available() and request.app.state.runtime.device_active(device))
    grants = identity.grants & settings.granted_permission_set
    return [
        Capability(
            name=d.name,
            description=d.description,
            risk_level=d.risk_level,
            requires_confirmation=d.requires_confirmation,
            required_permissions=d.required_permissions,
            target_device=device,
        )
        for d in TOOL_DEFINITIONS.values()
        if d.name in IMPLEMENTED
        and set(d.required_permissions).issubset(grants)
        and (d.name not in {"get_pc_status", "launch_app"} or live)
        and configured(d.name, settings)
    ]


@router.post("/devices/invitations")
def invite(
    payload: Invitation,
    request: Request,
    token=Depends(require_bearer_token),
    _approval=Depends(require_approval_credential),
    settings: Settings = Depends(get_settings),
):
    identity = caller_identity(request, token, settings)
    try:
        code = request.app.state.runtime.invite(identity, payload.grants)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"code": code, "expires_in_seconds": 120}


@router.post("/devices/enroll", response_model=SessionCredentials)
def enroll(payload: Enrollment, request: Request):
    try:
        return request.app.state.runtime.enroll(payload.code, payload.name)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/devices")
def devices(request: Request, token=Depends(require_bearer_token), settings: Settings = Depends(get_settings)):
    identity = caller_identity(request, token, settings)
    return {"devices": request.app.state.runtime.devices(identity)}


@router.delete("/devices/{device_id}")
def revoke(
    device_id: str,
    request: Request,
    token=Depends(require_bearer_token),
    _approval=Depends(require_approval_credential),
    settings: Settings = Depends(get_settings),
):
    try:
        request.app.state.runtime.revoke(caller_identity(request, token, settings), device_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    from app.tools.pending_actions import get_pending_action_store

    store = get_pending_action_store()
    store.invalidate_for_device(device_id)
    return {"revoked": True}


@router.post("/logout")
def logout(request: Request, token=Depends(require_bearer_token), settings: Settings = Depends(get_settings)):
    from app.tools.pending_actions import get_pending_action_store

    identity = caller_identity(request, token, settings)
    get_pending_action_store().invalidate_for_session(identity.session_id)
    request.app.state.runtime.logout(token)
    return {"logged_out": True}


@router.get("/capabilities", response_model=list[Capability])
def capabilities(
    request: Request,
    target_device: str = "desktop",
    token=Depends(require_bearer_token),
    settings: Settings = Depends(get_settings),
):
    return capabilities_for(request, caller_identity(request, token, settings), settings, target_device)


@router.get("/conversations")
def conversations(request: Request, token=Depends(require_bearer_token), settings: Settings = Depends(get_settings)):
    return {"conversations": request.app.state.runtime.conversations(caller_identity(request, token, settings))}


@router.get("/conversations/{conversation_id}")
def history(
    conversation_id: str, request: Request, token=Depends(require_bearer_token), settings: Settings = Depends(get_settings)
):
    store = request.app.state.runtime
    try:
        conversation = store.conversation(caller_identity(request, token, settings), conversation_id, "", "", "")
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {**conversation, "turns": store.history(conversation_id)}


@router.post("/turns", response_model=TurnResponse)
async def turn(
    payload: TurnRequest, request: Request, token=Depends(require_bearer_token), settings: Settings = Depends(get_settings)
):
    from app.api.tools import propose_tool

    identity = caller_identity(request, token, settings)
    store = request.app.state.runtime
    model = settings.local_llm_model if settings.reasoning_provider == "local" else settings.anthropic_model
    if settings.reasoning_provider == "mock":
        model = "deterministic-demo"
    try:
        conv = store.conversation(
            identity, payload.conversation_id, settings.reasoning_provider, model, prompt_version(settings)
        )
        served = (settings.reasoning_provider, model, prompt_version(settings))
        if (conv["provider"], conv["model"], conv["prompt_version"]) != served:
            raise ValueError("Model configuration changed; start a new conversation")
        existing = store.begin_turn(conv["id"], payload.turn_id, payload.question)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if existing:
        if existing["state"] != "completed":
            raise HTTPException(status_code=409, detail="Turn unfinished or failed; no automatic replay")
        return json.loads(existing["response"])
    context = []
    for previous in store.history(conv["id"])[-17:]:
        if previous["state"] == "completed":
            context.append(
                ContextItem(source="CONVERSATION_HISTORY", label="previous user request", content=previous["question"])
            )
            result = json.loads(previous["response"])
            observed = store.action_result(result.get("action_id", ""))
            if observed:
                context.append(ContextItem(source="TOOL_RESULT", label="verified prior execution", content=observed["output"]))
            context.append(
                ContextItem(source="CONVERSATION_HISTORY", label="previous assistant response", content=result["spoken_answer"])
            )
    available = [c.name for c in capabilities_for(request, identity, settings, payload.target_device)]
    try:
        provider_factory = getattr(request.app.state, "reasoning_factory", None)
        if provider_factory is None:
            if settings.live_mode and settings.reasoning_provider not in {"local", "anthropic"}:
                raise ValueError("A real reasoning provider must be configured for live operation")
            def provider_factory(name):
                return get_reasoning_provider(name, settings)
        result = await run_in_threadpool(
            provider_factory(settings.reasoning_provider).reason, context, payload.question, available
        )
        action_id, decision, summary = "", "answer", ""
        if result.proposed_tool_call:
            call = result.proposed_tool_call
            if call.tool_name not in available:
                raise ValueError("Proposed capability is not available on the selected device")
            proposal = propose_tool(
                ToolProposeRequest(
                    tool_name=call.tool_name,
                    target=call.target,
                    content=call.content,
                    arguments=call.arguments,
                    target_device=payload.target_device,
                ),
                request,
                token,
                settings,
            )
            action_id, decision, summary = proposal.action_id or "", proposal.decision, proposal.confirmation_summary or ""
        response = TurnResponse(
            conversation_id=conv["id"],
            turn_id=payload.turn_id,
            spoken_answer=result.spoken_answer,
            action_id=action_id,
            decision=decision,
            confirmation_summary=summary,
            provider=conv["provider"],
            model=conv["model"],
            prompt_version=conv["prompt_version"],
        )
        store.finish_turn(conv["id"], payload.turn_id, response.model_dump())
        return response
    except Exception as exc:
        store.finish_turn(conv["id"], payload.turn_id, {"error": "Provider or capability unavailable"}, "failed")
        raise HTTPException(status_code=503, detail="Provider or capability unavailable; no action was executed") from exc
