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


def mock_synthesize(text: str) -> bytes:
    """Returns a tiny placeholder WAV payload rather than calling a real TTS
    vendor. The primary voice path is on-device (AVSpeechSynthesizer); this
    endpoint exists for the optional remote high-quality voice (spec 11)."""
    return b"RIFF....WAVEfmt " + text.encode("utf-8")[:64]
