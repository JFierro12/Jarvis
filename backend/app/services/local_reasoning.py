import json
from typing import List, Optional

import httpx

from app.core.config import Settings
from app.models.schemas import ContextItem, ProposedToolCall, ReasonResponse
from app.services.anthropic_reasoning import (
    REASON_JSON_SCHEMA,
    SYSTEM_PROMPT,
    LanguageReasoningUnavailableError,
)


class LocalLanguageReasoningProvider:
    """Reasoning backed by a locally hosted Ollama server instead of a cloud API.

    Uses Ollama's native `/api/chat` (not the OpenAI-compat shim) with `format`
    set to the same JSON schema the Anthropic provider uses, so both providers
    are held to the identical prompt-injection / tool-proposal-only contract —
    see `app/services/anthropic_reasoning.py` and `docs/THREAT_MODEL.md`.
    """

    def __init__(self, settings: Settings, client: Optional[httpx.Client] = None):
        self._base_url = settings.local_llm_base_url.rstrip("/")
        self._model = settings.local_llm_model
        self._client = client or httpx.Client(timeout=settings.local_llm_timeout_seconds)

    def reason(self, context: List[ContextItem], question: str, available_tools: List[str]) -> ReasonResponse:
        user_content = self._render_user_message(context, question, available_tools)
        try:
            response = self._client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    "format": REASON_JSON_SCHEMA,
                    "stream": False,
                    "options": {"temperature": 0.2, "num_ctx": 8192},
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LanguageReasoningUnavailableError(f"local LLM unavailable: {exc}") from exc

        try:
            payload = response.json()
            content = payload["message"]["content"]
            parsed = json.loads(content)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise LanguageReasoningUnavailableError("local LLM response was not valid JSON") from exc

        proposed = parsed.get("proposed_tool_call")
        tool_call = (
            ProposedToolCall(tool_name=proposed["tool_name"], target=proposed["target"], arguments=proposed.get("arguments", {}))
            if proposed
            else None
        )
        return ReasonResponse(
            spoken_answer=parsed["spoken_answer"],
            proposed_tool_call=tool_call,
            requires_confirmation=parsed["requires_confirmation"],
        )

    @staticmethod
    def _render_user_message(context: List[ContextItem], question: str, available_tools: List[str]) -> str:
        rendered = "\n\n".join(f"[{item.source}: {item.label}]\n{item.content}" for item in context)
        tools = ", ".join(available_tools) if available_tools else "(none)"
        return f"{rendered}\n\nAvailable tools: {tools}\n\nQuestion: {question}"


__all__ = ["LocalLanguageReasoningProvider"]
