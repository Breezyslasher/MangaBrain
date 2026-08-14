"""Application settings, read from environment variables (and .env in dev)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://mangabrain:mangabrain@localhost:5432/mangabrain"
    # 384-dim, CPU-friendly, and a large retrieval-quality step over the
    # earlier all-MiniLM-L6-v2 (also 384-dim, so the vector column fits both).
    # Our similarity is symmetric (title text vs title text), so bge's query
    # instruction prefix is deliberately not used.
    embed_model: str = "BAAI/bge-small-en-v1.5"
    candidate_pool: int = 500
    http_cache_dir: str = ""
    anilist_min_interval: float = 2.0
    jikan_min_interval: float = 0.5
    sync_interval_hours: float = 24.0
    # Optional catalog snapshot to seed an empty database from (see
    # pipeline.restore). Best-effort: on failure the worker falls back to a
    # normal full sync.
    seed_snapshot_url: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

# Version of the text recipe fed to the embedding model (titles, genres,
# ranked tags, synopsis). Bump when the recipe changes: the composed id below
# then differs from stored rows, so pipeline.embed re-embeds everything as a
# normal migration.
EMBED_TEXT_VERSION = 3


def embed_model_id(model_name: str | None = None) -> str:
    """The identifier stored per embedding row: model plus text recipe."""
    return f"{model_name or settings.embed_model}#text-v{EMBED_TEXT_VERSION}"
