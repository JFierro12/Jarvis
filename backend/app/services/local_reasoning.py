import json
from typing import List, Optional

import httpx

from app.core.config import Settings
from app.models.schemas import ContextItem, ProposedToolCall, ReasonResponse
from app.services.anthropic_reasoning import (
    REASON_JSON_SCHEMA,
    SYSTEM_PROMPT,
    TUNED_SYSTEM_PROMPT,
    LanguageReasoningUnavailableError,
)

# The generation cap, named so a truncation error can cite it. See the comment
# at the call site for why a cap exists at all.
NUM_PREDICT = 700


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
        # "tuned" is only correct for a model fine-tuned against the short
        # prompt (training/README.md). Anything unrecognised falls back to the
        # full production prompt rather than silently sending a model rules it
        # was never taught — a wrong guess here degrades tool choice.
        self._system_prompt = (
            TUNED_SYSTEM_PROMPT
            if settings.local_llm_prompt_profile.strip().lower() == "tuned"
            else SYSTEM_PROMPT
        )

    def reason(self, context: List[ContextItem], question: str, available_tools: List[str]) -> ReasonResponse:
        user_content = self._render_user_message(context, question, available_tools)
        try:
            response = self._client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": self._system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "format": REASON_JSON_SCHEMA,
                    "stream": False,
                    # num_predict is a backstop, not the length policy -- the prompt sets
                    # that. Without a cap, an open-ended request ("explain in full
                    # depth the limits of your knowledge") made the 8B loop, emitting
                    # the same two sentences three times for ~400 words. 700 tokens is
                    # comfortably above any answer we actually want and still bounds
                    # the degenerate case.
                    "options": {"temperature": 0.2, "num_ctx": 8192, "num_predict": NUM_PREDICT},
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LanguageReasoningUnavailableError(f"local LLM unavailable: {exc}") from exc

        try:
            payload = response.json()
            content = payload["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LanguageReasoningUnavailableError("local LLM response was not valid JSON") from exc

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            # Ollama says why generation stopped. done_reason == "length" means
            # generation ran to num_predict without closing the JSON. That is a
            # different fault from the model emitting malformed output, and the
            # single shared message made it look like a bad model.
            #
            # It does NOT mean the answer needed more room. Measured on the one
            # case that reproduces it (a current-information question following
            # a turn that falsely claimed narrow knowledge), 6 calls per cap:
            # at 700 tokens 3/6 ran to exactly 700; at 900, 1/6 ran to exactly
            # 900; at 1200, 3/6 ran to exactly 1200. The calls that succeed
            # finish in 44-121 tokens. The distribution is bimodal: either a
            # normal answer or a repetition loop that consumes whatever cap it
            # is given, taking 18.6s at 700 and 30.3s at 1200. Raising the cap
            # buys a slower failure, so do not raise it in response to this
            # error.
            if isinstance(payload, dict) and payload.get("done_reason") == "length":
                raise LanguageReasoningUnavailableError(
                    f"local LLM ran to the {NUM_PREDICT}-token cap without closing "
                    "its JSON (usually a repetition loop, not a long answer)"
                ) from exc
            raise LanguageReasoningUnavailableError("local LLM response was not valid JSON") from exc

        proposed = parsed.get("proposed_tool_call")
        tool_call = (
            ProposedToolCall(
                tool_name=proposed["tool_name"],
                target=proposed["target"],
                content=proposed.get("content", ""),
                arguments=proposed.get("arguments", {}),
            )
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
