from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PC_AGENT_", env_file=".env", extra="ignore")

    pairing_token: str = "change-me"
    host: str = "127.0.0.1"
    port: int = 8765
    rate_limit_per_minute: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
