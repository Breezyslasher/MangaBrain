"""Application settings, read from environment variables (and .env in dev)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://mangabrain:mangabrain@localhost:5432/mangabrain"
    # Chosen by measured recall on the AniList recommendation-pair benchmark
    # (pipeline.benchmark, 4860 pairs, 5000 random negatives, CI run of
    # 2026-08-22): r@10 0.174 / r@50 0.371 / MRR 0.081 vs 0.136 / 0.316 /
    # 0.066 for GIST-small - roughly 7 standard errors, a 28% relative gain,
    # and it wins every metric. The other measured sizes lose on
    # value-per-parameter: Qwen3-Embedding-0.6B managed only +0.009 r@10
    # from 5x the parameters. Cost of base over small: 768-dim vectors
    # (per-dimension partial ANN indexes in db/schema.sql) and about 3x the
    # embed time at 109M params. Our similarity is symmetric (title text vs
    # title text), so no query instruction prefix is used.
    embed_model: str = "avsolatorio/GIST-Embedding-v0"
    # CPU threads torch may use while embedding. 0 = all cores minus one,
    # so a long embed pass cannot starve everything else on a small host
    # (the shared mini-PC problem); set an exact count to override.
    embed_threads: int = 0
    candidate_pool: int = 500
    http_cache_dir: str = ""
    anilist_min_interval: float = 2.0
    jikan_min_interval: float = 0.5
    sync_interval_hours: float = 24.0
    # Titles the nightly pass checks for an English dub. Fifty per AniList
    # request since the walk was paged, so 400 titles is 8 requests - the
    # comment here used to say "one request each", which was true when the
    # default was chosen and has not been since. The initial fill is still a
    # one-off `python -m pipeline.backfill_dub`; this keeps up with newly
    # synced entries. 0 disables it.
    dub_backfill_per_pass: int = 400
    # Titles the nightly pass measures for ALL dub languages. Also fifty per
    # request - one unfiltered query returns every language at once - so the
    # default is larger than the English budget for the same request cost.
    # Without this the language columns only ever hold what a manual run put
    # there, and every title synced afterwards stays blank. 0 disables it.
    dub_language_per_pass: int = 2000
    # A dub is often added months after a title airs, so a checked row
    # goes stale rather than final. The nightly pass spends its budget on
    # never-checked titles first and then on the stalest ones, so this is
    # the age at which a row becomes eligible again. 0 disables rechecks.
    dub_recheck_days: int = 90
    # Optional catalog snapshot to seed an empty database from (see
    # pipeline.restore). Best-effort: on failure the worker falls back to a
    # normal full sync.
    seed_snapshot_url: str = ""
    # Optional self-hosted Yamtrack instance for list exclusion (see
    # api/routers/yamtrack.py). Token comes from the Yamtrack profile page.
    yamtrack_url: str = ""
    yamtrack_token: str = ""
    # Public-exposure hardening (e.g. behind a Cloudflare Tunnel). AUTH_TOKEN
    # set = every API request must carry Authorization: Bearer <token> (the
    # SPA prompts once and remembers it); unset = open, for LAN-only use.
    # RATE_LIMIT_PER_MINUTE > 0 = per-client-IP cap on API requests.
    auth_token: str = ""
    rate_limit_per_minute: int = 0

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
