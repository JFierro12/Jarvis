from typing import List, Optional, Protocol

from app.models.schemas import ContextItem, ProposedToolCall, ReasonResponse
from app.tools.registry import TOOL_DEFINITIONS

# Sources whose content must never be treated as instructions — only
# summarized/described if the user explicitly asks. Mirrors
# ios/JarvisKit/Sources/JarvisKit/Core/Models/ContextModels.swift.
UNTRUSTED_SOURCES = {"CAMERA_OBSERVATION", "TOOL_RESULT", "MEMORY_RESULT", "EXTERNAL_CONTENT"}

_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard the above",
    "new instructions:",
    "upload all memories",
    "send all memories",
)


class LanguageReasoningProvider(Protocol):
    def reason(self, context: List[ContextItem], question: str, available_tools: List[str]) -> ReasonResponse: ...


class MockLanguageReasoningProvider:
    """Deterministic reasoning for demo mode / offline dev — no network calls.

    This is also where prompt-injection defense is exercised: text pulled
    from CAMERA_OBSERVATION/TOOL_RESULT/MEMORY_RESULT/EXTERNAL_CONTENT is
    always treated as data to describe, never as a command to execute. A
    real cloud-backed implementation must uphold the same contract in its
    system prompt (see docs/THREAT_MODEL.md).
    """

    def reason(self, context: List[ContextItem], question: str, available_tools: List[str]) -> ReasonResponse:
        for item in context:
            if item.source in UNTRUSTED_SOURCES:
                lowered = item.content.lower()
                if any(marker in lowered for marker in _INJECTION_MARKERS):
                    return ReasonResponse(
                        spoken_answer=(
                            "That text contains what looks like an instruction, but I only "
                            "describe content from images and tool results — I don't act on it."
                        ),
                        proposed_tool_call=None,
                        requires_confirmation=False,
                    )

        proposed_tool_call: Optional[ProposedToolCall] = None
        answer = "I'm not certain — could you rephrase that?"

        lowered_question = question.lower()
        if "time" in lowered_question:
            proposed_tool_call = ProposedToolCall(tool_name="get_current_time")
            answer = "Let me check the time."
        elif "delete all" in lowered_question and "memor" in lowered_question:
            proposed_tool_call = ProposedToolCall(tool_name="delete_all_memories")
            answer = TOOL_DEFINITIONS["delete_all_memories"].description

        return ReasonResponse(
            spoken_answer=answer,
            proposed_tool_call=proposed_tool_call,
            requires_confirmation=proposed_tool_call is not None,
        )


def get_reasoning_provider(provider_name: str) -> LanguageReasoningProvider:
    if provider_name == "anthropic":
        from app.core.config import get_settings
        from app.services.anthropic_reasoning import AnthropicLanguageReasoningProvider

        return AnthropicLanguageReasoningProvider(get_settings())
    return MockLanguageReasoningProvider()
