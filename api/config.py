"""Application settings, read from environment variables (and .env in dev)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://mangabrain:mangabrain@localhost:5432/mangabrain"
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    candidate_pool: int = 500
    http_cache_dir: str = ""
    anilist_min_interval: float = 2.0
    jikan_min_interval: float = 0.5
    sync_interval_hours: float = 24.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
