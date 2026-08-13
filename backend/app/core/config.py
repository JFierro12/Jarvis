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

    @property
    def auth_token_set(self) -> set[str]:
        return {t.strip() for t in self.auth_tokens.split(",") if t.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
