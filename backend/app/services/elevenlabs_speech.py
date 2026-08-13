from typing import Optional

import httpx


class SpeechSynthesisUnavailableError(Exception):
    pass


class ElevenLabsSpeechProvider:
    """Real cloned-voice TTS via ElevenLabs
    (https://elevenlabs.io/docs/api-reference/text-to-speech/convert). The
    voice itself must already exist in the user's ElevenLabs account —
    cloned there from their own audio sample — this only calls the
    synthesis endpoint for an existing voice_id.
    """

    def __init__(self, settings, client: Optional[httpx.Client] = None):
        self._api_key = settings.elevenlabs_api_key
        self._default_voice_id = settings.elevenlabs_voice_id
        self._client = client or httpx.Client(timeout=20.0)

    def synthesize(self, text: str, voice_id: str) -> bytes:
        # The iOS client's VoiceSettings.voiceIdentifier defaults to
        # "en-US-calm-default" (an Apple on-device voice name, meaningless
        # to ElevenLabs) — fall back to the server-configured default voice
        # whenever the caller didn't explicitly pick a real ElevenLabs id.
        resolved_voice_id = voice_id if voice_id and voice_id != "en-US-calm-default" else self._default_voice_id
        if not resolved_voice_id:
            raise SpeechSynthesisUnavailableError("no ElevenLabs voice id configured")
        if not self._api_key:
            raise SpeechSynthesisUnavailableError("no ElevenLabs API key configured")

        try:
            response = self._client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{resolved_voice_id}",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": self._api_key, "Content-Type": "application/json"},
                json={"text": text, "model_id": "eleven_multilingual_v2"},
            )
        except httpx.HTTPError as exc:
            raise SpeechSynthesisUnavailableError(f"ElevenLabs request failed: {exc}") from exc
        if response.status_code >= 400:
            # httpx's default HTTPStatusError message omits the response
            # body — ElevenLabs' actual error detail (bad key, voice not
            # found, quota exceeded, etc.) is in there and far more useful
            # than a bare status code.
            raise SpeechSynthesisUnavailableError(f"ElevenLabs {response.status_code}: {response.text}")
        return response.content
