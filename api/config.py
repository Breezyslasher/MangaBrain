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

# Version of the text recipe fed to the embedding model (titles, genres,
# ranked tags, synopsis). Bump when the recipe changes: the composed id below
# then differs from stored rows, so pipeline.embed re-embeds everything as a
# normal migration.
EMBED_TEXT_VERSION = 2


def embed_model_id(model_name: str | None = None) -> str:
    """The identifier stored per embedding row: model plus text recipe."""
    return f"{model_name or settings.embed_model}#text-v{EMBED_TEXT_VERSION}"
