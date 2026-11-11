from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_", env_file=".env", extra="ignore")

    auth_tokens: str = "dev-local-token"
    database_url: str = "sqlite:///./jarvis.db"
    reasoning_provider: str = "mock"
    vision_provider: str = "mock"
    reasoning_api_key: str = ""
    vision_api_key: str = ""
    # Defaults to Claude Opus 5 per Anthropic's current guidance; override for
    # cost-sensitive personal use (e.g. "claude-sonnet-5", "claude-haiku-4-5").
    anthropic_model: str = "claude-opus-5"
    log_level: str = "info"

    # "mock" (default, tiny placeholder payload, no network) or "elevenlabs"
    # for a real cloned-voice TTS backend.
    tts_provider: str = "mock"
    elevenlabs_api_key: str = ""
    # Default voice used when the client doesn't override voice_id.
    elevenlabs_voice_id: str = ""

    # "mock" (default, canned string) or "local" for real on-device
    # speech-to-text via faster-whisper — no cloud, works offline.
    stt_provider: str = "mock"
    # base.en transcribed clean test speech exactly; small.en is the next
    # step up in accuracy if real-world mic audio proves noisier.
    whisper_model: str = "base.en"
    # CPU on purpose, not as a fallback: Ollama already needs ~5GB of this
    # machine's 8GB VRAM, and since an LLM reply takes seconds, the ~0.7s
    # GPU would save on transcription isn't worth making the LLM spill
    # layers to CPU. Set "cuda" only on a card with headroom to spare —
    # CTranslate2 also needs CUDA/cuBLAS libraries present for that.
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    # Comma-separated folders JARVIS may read/write. Empty by default:
    # file tools refuse everything until a root is explicitly granted,
    # rather than defaulting to the whole drive or the process's cwd.
    allowed_folders: str = ""

    # Only read when reasoning_provider == "local". Points at a locally
    # running Ollama server (see desktop/README.md for Windows setup). The
    # model tag drifts over time — confirm the exact current one with
    # `ollama list` at setup rather than trusting this default blindly.
    local_llm_base_url: str = "http://localhost:11434"
    local_llm_model: str = "qwen2.5:14b-instruct-q4_K_M"
    # Generous — local inference on a consumer GPU is slower than a cloud API.
    local_llm_timeout_seconds: float = 60.0

    @property
    def auth_token_set(self) -> set[str]:
        return {t.strip() for t in self.auth_tokens.split(",") if t.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
