"""Single-user application settings, editable from the web UI.

Stores tracker account details (AniList/MAL/Kitsu usernames, Yamtrack
endpoint and token) in the database so they survive browser changes;
environment variables
remain as fallback for anything not set here. The Yamtrack token is write-only
through the API: responses only say whether one is configured.
"""

from fastapi import APIRouter

from api.config import settings
from api.db import get_pool
from api.models import AppSettingsIn, AppSettingsOut

router = APIRouter()

SETTING_KEYS = (
    "anilist_username",
    "mal_username",
    "kitsu_username",
    "yamtrack_url",
    "yamtrack_token",
)

UPSERT_SQL = """
    INSERT INTO app_settings (key, value, updated_at)
    VALUES (%s, %s, now())
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
"""


def read_settings() -> dict[str, str]:
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT key, value FROM app_settings WHERE key = ANY(%s)", (list(SETTING_KEYS),)
        ).fetchall()
    return {row["key"]: row["value"] for row in rows}


def effective_yamtrack_config() -> tuple[str, str]:
    """Yamtrack url and token: database settings first, env fallback."""
    stored = read_settings()
    url = stored.get("yamtrack_url") or settings.yamtrack_url
    token = stored.get("yamtrack_token") or settings.yamtrack_token
    return url, token


def _to_out(stored: dict[str, str]) -> AppSettingsOut:
    return AppSettingsOut(
        anilist_username=stored.get("anilist_username", ""),
        mal_username=stored.get("mal_username", ""),
        kitsu_username=stored.get("kitsu_username", ""),
        yamtrack_url=stored.get("yamtrack_url") or settings.yamtrack_url,
        yamtrack_token_set=bool(stored.get("yamtrack_token") or settings.yamtrack_token),
    )


@router.get("/settings", response_model=AppSettingsOut)
def get_settings() -> AppSettingsOut:
    return _to_out(read_settings())


@router.put("/settings", response_model=AppSettingsOut)
def update_settings(body: AppSettingsIn) -> AppSettingsOut:
    updates = {key: value.strip() for key, value in body.model_dump().items() if value is not None}
    if updates:
        with get_pool().connection() as conn:
            for key, value in updates.items():
                if value:
                    conn.execute(UPSERT_SQL, (key, value))
                else:
                    conn.execute("DELETE FROM app_settings WHERE key = %s", (key,))
    return _to_out(read_settings())
