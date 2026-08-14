"""Kitsu integration (https://kitsu.app, JSON:API).

Pulls the configured user's anime and manga library from Kitsu's public API
and stores it as the "kitsu" generic exclusion list. Kitsu media carry
"mappings" to external sites; the site names come from the server's
MappingExternalSite enum (myanimelist/anime, myanimelist/manga,
anilist/anime, anilist/manga), so library entries translate to the typed
MAL ids and AniList ids the exclusion filter joins on. Entries whose media
is hidden (adult titles expose no relationship data without auth) or has no
usable numeric mapping are counted as skipped. Exclude with
exclude_list=kitsu.

Set the Kitsu username (or numeric user id, shown in the profile URL) in
the Accounts panel. No token needed: libraries are public on Kitsu.
"""

import httpx
from fastapi import APIRouter, HTTPException

from api.models import ExclusionStatus
from api.routers.app_settings import read_settings
from api.routers.exclusions import get_status, store_exclusion_list
from pipeline.client import RateLimitedClient

router = APIRouter()

LIST_NAME = "kitsu"
API_BASE = "https://kitsu.app/api/edge"
PAGE_LIMIT = 500
KINDS = ("anime", "manga")


def harvest_entries(
    kind: str, entries: list[dict], included: list[dict]
) -> tuple[set[tuple[str, int]], int]:
    """Translate one kind's library entries into exclusion (kind, id) pairs.

    JSON:API response shape: each library entry references its media under
    relationships[kind].data, the media resources sit in `included` with
    their mapping references, and the mapping resources (externalSite,
    externalId) sit alongside them. externalId is a string and occasionally
    non-numeric (known Kitsu data bug), hence the isdigit guard.
    """
    media_by_id = {item["id"]: item for item in included if item.get("type") == kind}
    mapping_by_id = {item["id"]: item for item in included if item.get("type") == "mappings"}
    site_to_kind = {
        f"myanimelist/{kind}": f"mal_{kind}",
        f"anilist/{kind}": "anilist",
    }

    harvested: set[tuple[str, int]] = set()
    skipped = 0
    for entry in entries:
        ref = ((entry.get("relationships") or {}).get(kind) or {}).get("data") or {}
        media = media_by_id.get(ref.get("id"))
        refs = ((media or {}).get("relationships") or {}).get("mappings") or {}
        found: set[tuple[str, int]] = set()
        for mapping_ref in refs.get("data") or []:
            mapping = mapping_by_id.get(mapping_ref.get("id")) or {}
            attrs = mapping.get("attributes") or {}
            target = site_to_kind.get(attrs.get("externalSite"))
            ext_id = str(attrs.get("externalId") or "")
            if target and ext_id.isdigit():
                found.add((target, int(ext_id)))
        if found:
            harvested.update(found)
        else:
            skipped += 1
    return harvested, skipped


def _resolve_user_id(client: RateLimitedClient, username: str) -> str:
    if username.isdigit():
        return username
    data = client.request("GET", f"{API_BASE}/users", params={"filter[name]": username})
    users = data.get("data") or []
    if not users:
        raise HTTPException(status_code=404, detail=f"no Kitsu user named '{username}'")
    if len(users) > 1:
        raise HTTPException(
            status_code=422,
            detail="multiple Kitsu users match that name; use your numeric"
            " user id (visible in your Kitsu profile URL) instead",
        )
    return users[0]["id"]


def _fetch_library(
    client: RateLimitedClient, user_id: str, kind: str
) -> tuple[list[dict], list[dict]]:
    """Fetch all library entries of one kind, following links.next pages.

    The next link already carries every query parameter, so params are only
    sent with the first request.
    """
    url: str | None = f"{API_BASE}/library-entries"
    params: dict | None = {
        "filter[user_id]": user_id,
        "filter[kind]": kind,
        "include": f"{kind},{kind}.mappings",
        f"fields[{kind}]": "mappings",
        "fields[mappings]": "externalSite,externalId",
        "page[limit]": PAGE_LIMIT,
    }
    entries: list[dict] = []
    included: list[dict] = []
    while url:
        data = client.request("GET", url, params=params)
        entries.extend(data.get("data") or [])
        included.extend(data.get("included") or [])
        url = (data.get("links") or {}).get("next")
        params = None
    return entries, included


@router.post("/kitsu/refresh", response_model=ExclusionStatus)
def refresh_kitsu() -> ExclusionStatus:
    username = read_settings().get("kitsu_username", "").strip()
    if not username:
        raise HTTPException(status_code=422, detail="set the Kitsu username in settings first")
    client = RateLimitedClient(
        min_interval=0.2,
        extra_headers={"Accept": "application/vnd.api+json"},
    )
    all_entries: set[tuple[str, int]] = set()
    skipped = 0
    try:
        try:
            user_id = _resolve_user_id(client, username)
            for kind in KINDS:
                entries, included = _fetch_library(client, user_id, kind)
                harvested, kind_skipped = harvest_entries(kind, entries, included)
                all_entries.update(harvested)
                skipped += kind_skipped
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502, detail=f"Kitsu error {exc.response.status_code}"
            ) from exc
        except httpx.TransportError as exc:
            raise HTTPException(status_code=502, detail="Kitsu unreachable") from exc
    finally:
        client.close()

    store_exclusion_list(LIST_NAME, all_entries)
    status = get_status(LIST_NAME)
    status.skipped = skipped
    return status


@router.get("/kitsu", response_model=ExclusionStatus)
def kitsu_status() -> ExclusionStatus:
    return get_status(LIST_NAME)
