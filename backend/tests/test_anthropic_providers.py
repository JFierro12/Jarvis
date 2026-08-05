"""Tests for the real Anthropic-backed providers. These never call the real
API — a fake client stands in for `anthropic.Anthropic`, so the tests verify
request construction and response parsing without needing a key or network.
"""

import json
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.models.schemas import ContextItem
from app.services.anthropic_reasoning import AnthropicLanguageReasoningProvider, LanguageReasoningUnavailableError
from app.services.anthropic_vision import AnthropicVisionReasoningProvider
from app.services.vision import VisionUnavailableError


class _FakeMessages:
    def __init__(self, response_text):
        self.response_text = response_text
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.response_text)])


class _FakeAnthropicClient:
    def __init__(self, response_text):
        self.messages = _FakeMessages(response_text)


def _settings():
    return Settings(anthropic_model="claude-opus-5")


def test_reasoning_provider_parses_structured_response():
    payload = json.dumps({
        "spoken_answer": "It's 3:15 PM.",
        "requires_confirmation": False,
        "proposed_tool_call": {"tool_name": "get_current_time", "target": "", "arguments": {}},
    })
    fake_client = _FakeAnthropicClient(payload)
    provider = AnthropicLanguageReasoningProvider(_settings(), client=fake_client)

    context = [ContextItem(source="USER_REQUEST", label="transcript", content="what time is it")]
    result = provider.reason(context, "what time is it", ["get_current_time"])

    assert result.spoken_answer == "It's 3:15 PM."
    assert result.proposed_tool_call.tool_name == "get_current_time"
    assert fake_client.messages.last_kwargs["model"] == "claude-opus-5"
    assert fake_client.messages.last_kwargs["output_config"]["format"]["type"] == "json_schema"


def test_reasoning_provider_handles_no_tool_call():
    payload = json.dumps({"spoken_answer": "I'm not sure.", "requires_confirmation": False, "proposed_tool_call": None})
    fake_client = _FakeAnthropicClient(payload)
    provider = AnthropicLanguageReasoningProvider(_settings(), client=fake_client)

    result = provider.reason([], "tell me something", [])
    assert result.proposed_tool_call is None


def test_reasoning_provider_raises_on_invalid_json():
    fake_client = _FakeAnthropicClient("not json")
    provider = AnthropicLanguageReasoningProvider(_settings(), client=fake_client)

    with pytest.raises(LanguageReasoningUnavailableError):
        provider.reason([], "hello", [])


def test_reasoning_system_prompt_forbids_acting_on_untrusted_content():
    payload = json.dumps({"spoken_answer": "ok", "requires_confirmation": False, "proposed_tool_call": None})
    fake_client = _FakeAnthropicClient(payload)
    provider = AnthropicLanguageReasoningProvider(_settings(), client=fake_client)
    provider.reason([], "hi", [])
    system_prompt = fake_client.messages.last_kwargs["system"]
    assert "CAMERA_OBSERVATION" in system_prompt
    assert "untrusted" in system_prompt


def test_vision_provider_parses_structured_response():
    payload = json.dumps({
        "answer": "A laptop on a desk.",
        "confidence": 0.8,
        "detected_text": None,
        "uncertainty_note": None,
        "suggested_follow_up": None,
    })
    fake_client = _FakeAnthropicClient(payload)
    provider = AnthropicVisionReasoningProvider(_settings(), client=fake_client)

    result = provider.analyze("aGVsbG8=", "what am I looking at?")
    assert result.answer == "A laptop on a desk."
    assert result.confidence == 0.8
    image_block = fake_client.messages.last_kwargs["messages"][0]["content"][0]
    assert image_block["source"]["media_type"] == "image/jpeg"


def test_vision_provider_rejects_invalid_base64():
    fake_client = _FakeAnthropicClient("{}")
    provider = AnthropicVisionReasoningProvider(_settings(), client=fake_client)

    with pytest.raises(VisionUnavailableError):
        provider.analyze("not-valid-base64!!", "what is this?")
