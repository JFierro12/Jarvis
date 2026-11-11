import base64

import pytest
from fastapi.testclient import TestClient

import app.api.assistant as assistant_module
import app.memory.database as database_module
from app.core.config import Settings, get_settings
from app.main import app
from app.services.local_speech import LocalTranscriptionError
from tests.conftest import AUTH_HEADERS


class FakeTranscriber:
    """Stands in for LocalWhisperTranscriber so the suite never loads real
    Whisper weights (~150MB download, seconds of load time) just to check
    the endpoint's own request/response handling."""

    def __init__(self, text="jarvis open youtube", error=None):
        self.text = text
        self.error = error
        self.received = None

    async def transcribe(self, audio_bytes):
        self.received = audio_bytes
        if self.error:
            raise self.error
        return self.text


@pytest.fixture()
def local_stt_client(monkeypatch):
    def override_get_settings():
        return Settings(auth_tokens="dev-local-token", stt_provider="local")

    app.dependency_overrides[get_settings] = override_get_settings
    # The DB isn't touched by /v1/transcribe, but app startup still wires it.
    app.dependency_overrides.setdefault(database_module.get_db_session, lambda: None)

    fake = FakeTranscriber()
    monkeypatch.setattr(assistant_module, "get_local_transcriber", lambda *_args: fake)

    with TestClient(app) as client:
        yield client, fake

    app.dependency_overrides.clear()


def test_local_transcribe_returns_model_text(local_stt_client):
    client, fake = local_stt_client
    audio = b"fake audio bytes"
    response = client.post(
        "/v1/transcribe",
        json={"audio_base64": base64.b64encode(audio).decode(), "language": "en-US"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["text"] == "jarvis open youtube"
    # The decoded bytes must reach the transcriber unchanged — a base64
    # handling bug here would corrupt every utterance.
    assert fake.received == audio


def test_local_transcribe_rejects_invalid_base64(local_stt_client):
    client, _fake = local_stt_client
    response = client.post(
        "/v1/transcribe",
        json={"audio_base64": "not!valid!base64!", "language": "en-US"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400


def test_local_transcribe_rejects_empty_audio(local_stt_client):
    client, _fake = local_stt_client
    response = client.post(
        "/v1/transcribe",
        json={"audio_base64": "", "language": "en-US"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400


def test_local_transcribe_silence_is_not_an_error(local_stt_client):
    """Most captured chunks in wake-word mode are ambient room noise that
    transcribe to nothing. That has to come back as an ordinary empty
    result the caller can skip, not an error that spams the UI."""
    client, fake = local_stt_client
    fake.text = ""
    response = client.post(
        "/v1/transcribe",
        json={"audio_base64": base64.b64encode(b"silence").decode(), "language": "en-US"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["text"] == ""
    assert response.json()["confidence"] == 0.0


def test_local_transcribe_surfaces_model_failure(local_stt_client):
    client, fake = local_stt_client
    fake.error = LocalTranscriptionError("Could not load Whisper model")
    response = client.post(
        "/v1/transcribe",
        json={"audio_base64": base64.b64encode(b"audio").decode(), "language": "en-US"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 422
    assert "Whisper" in response.json()["detail"]


def test_transcribe_requires_auth(local_stt_client):
    client, _fake = local_stt_client
    response = client.post("/v1/transcribe", json={"audio_base64": "AAAA", "language": "en-US"})
    assert response.status_code == 401
