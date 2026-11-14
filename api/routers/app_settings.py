"""Application settings, editable from the web UI, per profile.

Stores tracker account details (AniList/MAL/Kitsu usernames, Yamtrack
endpoint and token, Hardcover token, TMDB key) in the database so they survive
browser changes; environment variables remain as fallback for anything not
set here. The Yamtrack, Hardcover and TMDB secrets are write-only through the
API: responses only say whether one is configured.
"""

import json

from fastapi import APIRouter, HTTPException

from api import profiles
from api.config import settings
from api.datasets import dataset_for_medium, read_install, request_install
from api.db import get_pool
from api.media_groups import MEDIUM_GROUPS
from api.models import AppSettingsIn, AppSettingsOut

router = APIRouter()

# Each profile's own (api.profiles): whose lists to fetch and exclude.
PROFILE_KEYS = (
    "anilist_username",
    "mal_username",
    "kitsu_username",
    "yamtrack_url",
    "yamtrack_token",
    "hardcover_token",
)
# Shared: the TMDB key only downloads public catalogue data.
SHARED_KEYS = ("tmdb_api_key",)
SETTING_KEYS = PROFILE_KEYS + SHARED_KEYS


def stored_key(key: str) -> str:
    return profiles.setting_key(key) if key in PROFILE_KEYS else key


def env_fallback(value: str) -> str:
    """Environment settings are the server owner's: the default profile's
    fallback, never another person's."""
    return value if profiles.current() == profiles.DEFAULT else ""


UPSERT_SQL = """
    INSERT INTO app_settings (key, value, updated_at)
    VALUES (%s, %s, now())
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
"""


def read_settings() -> dict[str, str]:
    """The current profile's settings, plus the shared ones, by plain key."""
    wanted = {stored_key(key): key for key in SETTING_KEYS}
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT key, value FROM app_settings WHERE key = ANY(%s)", (list(wanted),)
        ).fetchall()
    return {wanted[row["key"]]: row["value"] for row in rows}


def effective_yamtrack_config() -> tuple[str, str]:
    """Yamtrack url and token: database settings first, env fallback."""
    stored = read_settings()
    url = stored.get("yamtrack_url") or env_fallback(settings.yamtrack_url)
    token = stored.get("yamtrack_token") or env_fallback(settings.yamtrack_token)
    return url, token


def effective_hardcover_token() -> str:
    """Hardcover API token: database setting first, env fallback."""
    return read_settings().get("hardcover_token") or env_fallback(settings.hardcover_token)


# Stored apart from SETTING_KEYS: a list, not a string, and a value the
# server acts on - switching a medium on can start a download.
ENABLED_MEDIA_KEY = "enabled_media"


def stored_enabled_media(conn) -> list[str] | None:
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = %s", (ENABLED_MEDIA_KEY,)
    ).fetchone()
    if row is None:
        return None
    try:
        chosen = json.loads(row["value"])
    except (TypeError, ValueError):
        return None
    # A medium the server no longer has is dropped rather than shown.
    return [g for g in MEDIUM_GROUPS if g in chosen] or None


def enabled_media(conn) -> list[str]:
    """What the reader chose, or until they choose, every medium with data.

    Not every medium: an install without books would show an empty Books
    tab, and the first save that included it would start a 3.5 GB
    download nobody asked for.
    """
    chosen = stored_enabled_media(conn)
    if chosen is not None:
        return chosen
    from api.routers.datasets import installed_keys

    present = installed_keys(conn)
    return [g for g in MEDIUM_GROUPS if (d := dataset_for_medium(g)) and d.key in present]


def _save_enabled_media(conn, wanted: list[str]) -> None:
    unknown = sorted(set(wanted) - set(MEDIUM_GROUPS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown media: {', '.join(unknown)}")
    if not wanted:
        raise HTTPException(status_code=422, detail="at least one medium must stay on")
    from api.routers.datasets import installed_keys

    before = set(enabled_media(conn))
    present = installed_keys(conn)
    conn.execute(UPSERT_SQL, (ENABLED_MEDIA_KEY, json.dumps(wanted)))
    # Only what was just switched on. A medium left on is not a new
    # request, so re-saving the page never starts a download; and an
    # install already under way is left alone by request_install.
    for group in set(wanted) - before:
        dataset = dataset_for_medium(group)
        if dataset is None or not dataset.installable or dataset.key in present:
            continue
        current = read_install(conn, dataset.key)
        if current is None or current.get("state") in ("done", "failed"):
            request_install(conn, dataset.key)


def _to_out(stored: dict[str, str], media: list[str]) -> AppSettingsOut:
    return AppSettingsOut(
        anilist_username=stored.get("anilist_username", ""),
        mal_username=stored.get("mal_username", ""),
        kitsu_username=stored.get("kitsu_username", ""),
        yamtrack_url=stored.get("yamtrack_url") or env_fallback(settings.yamtrack_url),
        yamtrack_token_set=bool(
            stored.get("yamtrack_token") or env_fallback(settings.yamtrack_token)
        ),
        hardcover_token_set=bool(
            stored.get("hardcover_token") or env_fallback(settings.hardcover_token)
        ),
        tmdb_api_key_set=bool(stored.get("tmdb_api_key") or settings.tmdb_api_key),
        profile=profiles.label(),
        enabled_media=media,
    )


def current_enabled_media() -> list[str]:
    with get_pool().connection() as conn:
        return enabled_media(conn)


@router.get("/settings", response_model=AppSettingsOut)
def get_settings() -> AppSettingsOut:
    return _to_out(read_settings(), current_enabled_media())


@router.put("/settings", response_model=AppSettingsOut)
def update_settings(body: AppSettingsIn) -> AppSettingsOut:
    fields = body.model_dump(exclude={"enabled_media"})
    updates = {key: value.strip() for key, value in fields.items() if value is not None}
    with get_pool().connection() as conn:
        if body.enabled_media is not None:
            _save_enabled_media(conn, list(dict.fromkeys(body.enabled_media)))
        for key, value in updates.items():
            if value:
                conn.execute(UPSERT_SQL, (stored_key(key), value))
            else:
                conn.execute("DELETE FROM app_settings WHERE key = %s", (stored_key(key),))
        media = enabled_media(conn)
    return _to_out(read_settings(), media)
