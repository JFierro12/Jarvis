import base64


def mock_transcribe(audio_base64: str) -> str:
    """Deterministic stand-in for a cloud STT fallback. The primary
    transcription path is on-device (Apple Speech framework); this endpoint
    exists for the optional cloud fallback described in spec 10."""
    try:
        base64.b64decode(audio_base64, validate=True)
    except Exception:
        return ""
    return "what am I looking at"


class MockSpeechProvider:
    """Returns a tiny placeholder WAV payload rather than calling a real TTS
    vendor. The primary voice path is on-device (AVSpeechSynthesizer); this
    is the default until `JARVIS_TTS_PROVIDER=elevenlabs` is configured with
    a real cloned voice (spec 11)."""

    def synthesize(self, text: str, voice_id: str) -> bytes:
        return b"RIFF....WAVEfmt " + text.encode("utf-8")[:64]


def get_speech_provider(provider_name: str):
    if provider_name == "elevenlabs":
        from app.core.config import get_settings
        from app.services.elevenlabs_speech import ElevenLabsSpeechProvider

        return ElevenLabsSpeechProvider(get_settings())
    return MockSpeechProvider()
