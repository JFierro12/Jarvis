"""Tests for the local Ollama-backed reasoning provider. These never call a
real Ollama server — an httpx.MockTransport stands in for the network, so the
tests verify request construction and response parsing/error mapping without
needing a running local LLM.
"""

import json

import httpx
import pytest

from app.core.config import Settings
from app.models.schemas import ContextItem
from app.services.anthropic_reasoning import LanguageReasoningUnavailableError
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
