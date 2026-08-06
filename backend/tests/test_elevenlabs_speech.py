"""Tests for the ElevenLabs-backed speech provider. These never call the
real API — a fake httpx client stands in, so the tests verify request
construction and response handling without needing a key or network.
"""

import pytest

from app.core.config import Settings
from app.services.elevenlabs_speech import ElevenLabsSpeechProvider, SpeechSynthesisUnavailableError


class _FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200, text: str = ""):
        self.content = content
        self.status_code = status_code
        self.text = text


class _FakeHTTPClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.last_url = None
        self.last_kwargs = None

    def post(self, url, **kwargs):
        self.last_url = url
        self.last_kwargs = kwargs
        return self._response


def _settings(**overrides):
    defaults = {"elevenlabs_api_key": "test-key", "elevenlabs_voice_id": "default-voice-id"}
    defaults.update(overrides)
    return Settings(**defaults)


def test_synthesize_uses_configured_default_voice_when_client_sends_apple_placeholder():
    fake_client = _FakeHTTPClient(_FakeResponse(b"audio-bytes"))
    provider = ElevenLabsSpeechProvider(_settings(), client=fake_client)

    audio = provider.synthesize("hello there", "en-US-calm-default")

    assert audio == b"audio-bytes"
    assert fake_client.last_url == "https://api.elevenlabs.io/v1/text-to-speech/default-voice-id"
    assert fake_client.last_kwargs["headers"]["xi-api-key"] == "test-key"
    assert fake_client.last_kwargs["json"]["text"] == "hello there"


def test_synthesize_uses_explicit_voice_id_when_given():
    fake_client = _FakeHTTPClient(_FakeResponse(b"audio-bytes"))
    provider = ElevenLabsSpeechProvider(_settings(), client=fake_client)

    provider.synthesize("hello", "some-other-real-voice-id")

    assert fake_client.last_url == "https://api.elevenlabs.io/v1/text-to-speech/some-other-real-voice-id"


def test_synthesize_raises_when_no_voice_id_configured_at_all():
    fake_client = _FakeHTTPClient(_FakeResponse(b""))
    provider = ElevenLabsSpeechProvider(_settings(elevenlabs_voice_id=""), client=fake_client)

    with pytest.raises(SpeechSynthesisUnavailableError):
        provider.synthesize("hello", "en-US-calm-default")


def test_synthesize_raises_when_no_api_key_configured():
    fake_client = _FakeHTTPClient(_FakeResponse(b""))
    provider = ElevenLabsSpeechProvider(_settings(elevenlabs_api_key=""), client=fake_client)

    with pytest.raises(SpeechSynthesisUnavailableError):
        provider.synthesize("hello", "en-US-calm-default")


def test_synthesize_raises_on_http_error_and_includes_response_body():
    fake_client = _FakeHTTPClient(_FakeResponse(b"", status_code=401, text='{"detail":"invalid_api_key"}'))
    provider = ElevenLabsSpeechProvider(_settings(), client=fake_client)

    with pytest.raises(SpeechSynthesisUnavailableError, match="invalid_api_key"):
        provider.synthesize("hello", "en-US-calm-default")
