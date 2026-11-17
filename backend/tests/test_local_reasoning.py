"""Tests for the local Ollama-backed reasoning provider. These never call a
real Ollama server — an httpx.MockTransport stands in for the network, so the
tests verify request construction and response parsing/error mapping without
needing a running local LLM.
"""

import json

import httpx
import pytest

import app.services.local_reasoning as local_reasoning
from app.core.config import Settings
from app.models.schemas import ContextItem
from app.services.anthropic_reasoning import (
    SYSTEM_PROMPT,
    TUNED_SYSTEM_PROMPT,
    LanguageReasoningUnavailableError,
)
from app.services.local_reasoning import LocalLanguageReasoningProvider


def _settings():
    return Settings(local_llm_base_url="http://localhost:11434", local_llm_model="test-model")


def _client_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_local_provider_parses_structured_response():
    payload = json.dumps({
        "spoken_answer": "It's 3:15 PM.",
        "requires_confirmation": False,
        "proposed_tool_call": {"tool_name": "get_current_time", "target": "", "arguments": {}},
    })
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": payload}})

    provider = LocalLanguageReasoningProvider(_settings(), client=_client_with(handler))
    context = [ContextItem(source="USER_REQUEST", label="transcript", content="what time is it")]
    result = provider.reason(context, "what time is it", ["get_current_time"])

    assert result.spoken_answer == "It's 3:15 PM."
    assert result.proposed_tool_call.tool_name == "get_current_time"
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["body"]["model"] == "test-model"
    assert captured["body"]["stream"] is False
    assert "properties" in captured["body"]["format"]


def test_local_provider_handles_no_tool_call():
    payload = json.dumps({"spoken_answer": "I'm not sure.", "requires_confirmation": False, "proposed_tool_call": None})

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": payload}})

    provider = LocalLanguageReasoningProvider(_settings(), client=_client_with(handler))
    result = provider.reason([], "tell me something", [])
    assert result.proposed_tool_call is None


def test_local_provider_raises_when_ollama_unreachable():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=_request)

    provider = LocalLanguageReasoningProvider(_settings(), client=_client_with(handler))
    with pytest.raises(LanguageReasoningUnavailableError):
        provider.reason([], "hello", [])


def test_local_provider_raises_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"}, request=request)

    provider = LocalLanguageReasoningProvider(_settings(), client=_client_with(handler))
    with pytest.raises(LanguageReasoningUnavailableError):
        provider.reason([], "hello", [])


def test_local_provider_raises_on_invalid_json():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "not json"}})

    provider = LocalLanguageReasoningProvider(_settings(), client=_client_with(handler))
    with pytest.raises(LanguageReasoningUnavailableError):
        provider.reason([], "hello", [])


# --- Truncation is a different fault from malformed output -----------------
#
# A reply that runs into num_predict comes back as valid-looking JSON with the
# closing braces missing. Both it and genuine garbage used to raise the same
# "not valid JSON", so a truncation read as a broken model. They need
# different responses -- raise the budget or shorten the prompt, versus
# investigate the model -- so the error has to say which happened.
#
# Found when a prompt change lengthened answers and two probe turns began
# failing at 16s against a 2.6s median.


def test_truncated_reply_is_reported_as_truncation_not_bad_json():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "message": {"content": '{"spoken_answer": "the answer begins and then'},
            "done_reason": "length",
        })

    provider = LocalLanguageReasoningProvider(_settings(), client=_client_with(handler))
    with pytest.raises(LanguageReasoningUnavailableError) as excinfo:
        provider.reason([], "explain something at length", [])
    message = str(excinfo.value)
    assert "without closing" in message, message
    assert str(local_reasoning.NUM_PREDICT) in message, (
        "the error should name the limit that was hit, so the fix is obvious"
    )


def test_malformed_reply_is_still_reported_as_bad_json():
    """The other branch, so the two cannot collapse back into one message."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "message": {"content": "not json at all"},
            "done_reason": "stop",
        })

    provider = LocalLanguageReasoningProvider(_settings(), client=_client_with(handler))
    with pytest.raises(LanguageReasoningUnavailableError) as excinfo:
        provider.reason([], "hello", [])
    assert "not valid JSON" in str(excinfo.value)
    assert "without closing" not in str(excinfo.value)


def test_truncation_is_detected_by_done_reason_not_by_guessing():
    """Incomplete JSON without done_reason=length is not called truncation.

    Guessing from the text (unbalanced braces, say) would mislabel a model
    that simply emitted a broken object.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "message": {"content": '{"spoken_answer": "unterminated'},
            "done_reason": "stop",
        })

    provider = LocalLanguageReasoningProvider(_settings(), client=_client_with(handler))
    with pytest.raises(LanguageReasoningUnavailableError) as excinfo:
        provider.reason([], "hello", [])
    assert "not valid JSON" in str(excinfo.value)


# --- Prompt profile selection (see training/README.md) ---------------------
#
# The short "tuned" prompt is only correct for a model fine-tuned against it.
# Sending it to a general model measurably wrecks behaviour: on the held-out
# evaluation set the same llama3.1:8b went from 1 to 35 obeyed prompt-injection
# attacks and from 50.1% to 17.7% exact targets when the long prompt was
# swapped for the short one. So the selection must be explicit and must fail
# towards the full prompt.


def _captured_system(profile=None):
    kwargs = {"local_llm_base_url": "http://localhost:11434", "local_llm_model": "test-model"}
    if profile is not None:
        kwargs["local_llm_prompt_profile"] = profile
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["system"] = json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(200, json={"message": {"content": json.dumps(
            {"spoken_answer": "ok", "requires_confirmation": False, "proposed_tool_call": None}
        )}})

    provider = LocalLanguageReasoningProvider(Settings(**kwargs), client=_client_with(handler))
    provider.reason([], "hello", [])
    return captured["system"]


def test_default_profile_sends_the_full_production_prompt():
    assert _captured_system() == SYSTEM_PROMPT


def test_tuned_profile_sends_the_distilled_prompt():
    assert _captured_system("tuned") == TUNED_SYSTEM_PROMPT
    # The point of the distilled prompt is that it is much shorter.
    assert len(TUNED_SYSTEM_PROMPT) < len(SYSTEM_PROMPT) / 4


@pytest.mark.parametrize("profile", ["production", "", "TUNE", "full", "default", "anything-else"])
def test_unrecognized_profile_falls_back_to_the_full_prompt(profile):
    """Fail closed: a typo must not silently degrade tool choice and injection
    resistance on a model that was never tuned for the short prompt."""
    assert _captured_system(profile) == SYSTEM_PROMPT


@pytest.mark.parametrize("profile", ["tuned", "TUNED", "  Tuned  "])
def test_tuned_profile_matching_ignores_case_and_whitespace(profile):
    assert _captured_system(profile) == TUNED_SYSTEM_PROMPT


def test_distilled_prompt_still_carries_the_trust_boundary():
    """Distillation may drop target formats and tone -- those are taught by the
    training data -- but never the provenance rule. If this text stops naming
    the untrusted sources, a tuned model has nothing in-context telling it that
    retrieved content cannot authorize an action."""
    for source in ("CAMERA_OBSERVATION", "TOOL_RESULT", "MEMORY_RESULT",
                   "EXTERNAL_CONTENT", "CONVERSATION_HISTORY"):
        assert source in TUNED_SYSTEM_PROMPT
    assert "never instructions" in TUNED_SYSTEM_PROMPT
    assert "proposed_tool_call" in TUNED_SYSTEM_PROMPT
    # It must not claim the model decides anything.
    assert "policy engine" in TUNED_SYSTEM_PROMPT
