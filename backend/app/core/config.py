import os
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# Which dotenv file to read, overridable so tests can point at a path
# that does not exist. Disabling dotenv alone is not isolation — process
# environment variables still apply — so tests must neutralize both.
_ENV_FILE = os.environ.get("JARVIS_ENV_FILE", ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_", env_file=_ENV_FILE, extra="ignore")

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

    # Credential for the human-approval path, deliberately separate from
    # auth_tokens. The packaged desktop host generates one per launch and
    # hands it to its own UI; it is never served in page HTML. Empty means
    # no approval path exists (fail closed), not an open one.
    approval_key: str = ""

    # Bumped when policy-relevant configuration changes, so approvals
    # decided under the old rules stop being spendable.
    policy_revision: str = "v1"
    live_mode: bool = False
    runtime_database: str = "jarvis-runtime.db"
    allowed_hosts: str = "127.0.0.1,localhost,testserver"
    allowed_origins: str = "http://127.0.0.1:8756,http://localhost:8756"
    local_agent_enabled: bool = False
    local_device_id: str = "desktop"
    remote_bind_host: str = ""
    remote_port: int = 8757
    tls_certificate: str = ""
    tls_private_key: str = ""

    # Capabilities this installation grants, comma-separated (e.g.
    # "os_control,file_access,screen_capture"). The authority for
    # permissions lives here, server-side: the client used to send its own
    # `granted_permissions` list, which meant any caller could claim any
    # capability and the policy check became decorative. Empty by default
    # so a fresh install grants nothing until asked.
    granted_permissions: str = ""

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
    # Which system prompt the *local* provider sends. "production" is the full
    # ~4k-token prompt in anthropic_reasoning.py, which a general-purpose local
    # model needs in order to behave. "tuned" sends the short distilled prompt
    # from training/jarvis_train/contract.py, and is only correct for a model
    # fine-tuned against it (see training/README.md) — the behaviour those
    # 4k tokens describe then lives in the weights instead, which cuts prefill
    # on every voice turn. Wrong setting, wrong behaviour, so it defaults to
    # the safe one and must be opted into explicitly.
    local_llm_prompt_profile: str = "production"

    # --- Location, weather and local news -------------------------------
    # The user's saved default location. Empty by default and never inferred:
    # the backend machine's timezone, locale and IP address are all wrong
    # answers to "where is the user", so with nothing set here JARVIS asks for
    # a city instead of guessing one. Latitude/longitude without each other is
    # treated as unconfigured, not as half a location.
    home_location_label: str = ""
    home_latitude: Optional[float] = None
    home_longitude: Optional[float] = None
    home_timezone: str = ""
    home_country_code: str = ""
    # "us" (Fahrenheit/mph/inch) or "metric" (Celsius/km·h⁻¹/mm).
    preferred_units: str = "us"

    # "mock" (default, deterministic, no network), "nws" for the US National
    # Weather Service, or "open_meteo". NWS needs no API key and is the only
    # one verified end-to-end from this machine; see docs/OVERNIGHT_LOG.md for
    # why the Open-Meteo forecast host could not be reached here.
    weather_provider: str = "mock"
    # NWS asks automated clients to identify themselves. Generic by design —
    # never put a personal email address here; it is sent to a third party on
    # every request.
    weather_user_agent: str = "JarvisAssistant/0.1 (personal assistant)"
    weather_cache_seconds: float = 600.0
    weather_timeout_seconds: float = 12.0

    # "mock" (default) or "rss". Comma-separated feed URLs; each may be
    # prefixed "<region>|<url>" to mark it as local to a place.
    news_provider: str = "mock"
    news_feeds: str = ""
    news_cache_seconds: float = 900.0
    news_timeout_seconds: float = 12.0
    news_max_items: int = 8

    @property
    def granted_permission_set(self) -> set:
        return {p.strip() for p in self.granted_permissions.split(",") if p.strip()}

    @property
    def auth_token_set(self) -> set[str]:
        return {t.strip() for t in self.auth_tokens.split(",") if t.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
