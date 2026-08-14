"""Yamtrack integration (self-hosted tracker, https://github.com/FuzzyGrim/Yamtrack).

Pulls the configured user's anime and manga lists from Yamtrack's REST API
(GET /api/v1/media/{type}, Bearer token auth, verified against the
feat/add-api branch) and stores the MAL-sourced entries as the "yamtrack"
generic exclusion list. Yamtrack uses MAL as its metadata source for anime
and manga, so ids map directly; entries from other sources are counted as
skipped. Exclude with exclude_list=yamtrack.

Configure YAMTRACK_URL (e.g. http://192.168.1.5:8000) and YAMTRACK_TOKEN
(the account token from Yamtrack's profile settings).
"""

import httpx
from fastapi import APIRouter, HTTPException

from api.models import ExclusionStatus
from api.routers.app_settings import effective_yamtrack_config
from api.routers.exclusions import get_status, store_exclusion_list
from pipeline.client import RateLimitedClient

router = APIRouter()

LIST_NAME = "yamtrack"
PAGE_LIMIT = 500
MEDIA_TYPES = (("anime", "mal_anime"), ("manga", "mal_manga"))


def _fetch_media(client: RateLimitedClient, base: str, media_type: str) -> list[dict]:
    results: list[dict] = []
    offset = 0
    while True:
        data = client.request(
            "GET",
            f"{base}/api/v1/media/{media_type}",
            params={"limit": PAGE_LIMIT, "offset": offset},
        )
        page = data.get("results") or []
        results.extend(page)
        total = (data.get("pagination") or {}).get("total", 0)
        offset += len(page)
        if not page or offset >= total:
            return results


@router.post("/yamtrack/refresh", response_model=ExclusionStatus)
def refresh_yamtrack() -> ExclusionStatus:
    url, token = effective_yamtrack_config()
    if not url or not token:
        raise HTTPException(
            status_code=422,
            detail="configure the Yamtrack URL and token in settings first",
        )
    base = url.rstrip("/")
    client = RateLimitedClient(
        min_interval=0.05,
        extra_headers={"Authorization": f"Bearer {token}"},
    )
    entries: dict[tuple[str, int], bool] = {}
    skipped = 0
    try:
        for media_type, kind in MEDIA_TYPES:
            try:
                rows = _fetch_media(client, base, media_type)
            except httpx.HTTPStatusError as exc:
                detail = f"Yamtrack error {exc.response.status_code}"
                if exc.response.status_code in (401, 403):
                    detail = "Yamtrack rejected the token (check YAMTRACK_TOKEN)"
                raise HTTPException(status_code=502, detail=detail) from exc
            except httpx.TransportError as exc:
                raise HTTPException(
                    status_code=502, detail="Yamtrack unreachable (check YAMTRACK_URL)"
                ) from exc
            for row in rows:
                item = row.get("item") or {}
                media_id = item.get("media_id")
                if item.get("source") != "mal" or not str(media_id or "").isdigit():
                    skipped += 1
                    continue
                # Yamtrack's API serializes status numerically (its
                # MEDIA_STATUS_MAP): 0 Planning, 1 In progress, 2 Paused,
                # 3 Completed, 4 Dropped. Only Planning counts as planned;
                # if an id appears both planned and started, started wins.
                planned = row.get("status") == 0
                key = (kind, int(media_id))
                entries[key] = entries.get(key, True) and planned
    finally:
        client.close()

    store_exclusion_list(LIST_NAME, entries)
    status = get_status(LIST_NAME)
    status.skipped = skipped
    return status


@router.get("/yamtrack", response_model=ExclusionStatus)
def yamtrack_status() -> ExclusionStatus:
    return get_status(LIST_NAME)
