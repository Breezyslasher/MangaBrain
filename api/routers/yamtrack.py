"""Yamtrack integration (self-hosted tracker, https://github.com/FuzzyGrim/Yamtrack).

Pulls the configured user's anime, manga and book lists from Yamtrack's
REST API (GET /api/v1/media/{type}, Bearer token auth, verified against the
feat/add-api branch) and stores them as the "yamtrack" generic exclusion
list. Exclude with exclude_list=yamtrack.

How each source maps onto the catalog:
- anime/manga from MAL: the MAL id, joined on id_mal by kind.
- anime/manga from AniList: the AniList id, which is media.id.
- books from Open Library: Yamtrack stores an edition id ("OL7353617M");
  the catalog holds works ("/works/OL45804W"). Each edition is resolved to
  its work through Open Library's public API, and the work to the book's
  media.id, stored under kind "anilist" - the kind that matches media.id
  directly, whatever the row's source.
Anything else (Hardcover books, MangaUpdates, manual entries, a work not in
the catalog) is counted as skipped.

Configure YAMTRACK_URL (e.g. http://192.168.1.5:8000) and YAMTRACK_TOKEN
(the account token from Yamtrack's profile settings).
"""

import httpx
from fastapi import APIRouter, HTTPException

from api import profiles
from api.config import settings
from api.models import ExclusionStatus
from api.openlibrary_match import book_ids_by_work, openlibrary_client, openlibrary_work_key
from api.routers.app_settings import effective_yamtrack_config
from api.routers.exclusions import get_status, store_exclusion_list
from pipeline.client import RateLimitedClient

router = APIRouter()

LIST_NAME = "yamtrack"
PAGE_LIMIT = 500
MEDIA_TYPES = (("anime", "mal_anime"), ("manga", "mal_manga"), ("book", None))


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
        max_retries=settings.interactive_max_retries,
        timeout=settings.interactive_timeout,
        extra_headers={"Authorization": f"Bearer {token}"},
    )
    entries: dict[tuple[str, int], tuple[bool, int | None]] = {}
    skipped = 0

    def add(key: tuple[str, int], row: dict) -> None:
        # Yamtrack's API serializes status numerically (its
        # MEDIA_STATUS_MAP): 0 Planning, 1 In progress, 2 Paused,
        # 3 Completed, 4 Dropped. Only Planning counts as planned;
        # if an id appears both planned and started, started wins,
        # and the higher score (0-10, normalized to 0-100) is kept.
        planned = row.get("status") == 0
        raw = row.get("score")
        score = int(round(float(raw) * 10)) if isinstance(raw, (int, float)) else None
        prev_planned, prev_score = entries.get(key, (True, None))
        scores = [s for s in (prev_score, score) if s is not None]
        entries[key] = (prev_planned and planned, max(scores) if scores else None)

    # Rows waiting on an edition-to-work lookup: (work key, row).
    books: list[tuple[str, dict]] = []
    # Open Library is a different host and takes no Yamtrack token, so it
    # gets its own client, paced for a public API and cached on disk.
    openlibrary = openlibrary_client()
    try:
        for media_type, kind in MEDIA_TYPES:
            try:
                rows = _fetch_media(client, base, media_type)
            except httpx.HTTPStatusError as exc:
                if media_type == "book" and exc.response.status_code in (400, 404):
                    # An instance that predates books, or has the type
                    # turned off: its anime and manga still count.
                    continue
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
                source = item.get("source")
                media_id = str(item.get("media_id") or "")
                if kind is None:
                    work = (
                        openlibrary_work_key(media_id, openlibrary)
                        if source == "openlibrary"
                        else None
                    )
                    if work is None:
                        skipped += 1
                    else:
                        books.append((work, row))
                elif source == "mal" and media_id.isdigit():
                    add((kind, int(media_id)), row)
                elif source == "anilist" and media_id.isdigit():
                    add(("anilist", int(media_id)), row)
                else:
                    skipped += 1
    finally:
        client.close()
        openlibrary.close()

    by_work = book_ids_by_work(sorted({work for work, _ in books}))
    for work, row in books:
        media_id = by_work.get(work)
        if media_id is None:
            # A book the catalog does not hold (not imported, or filtered
            # out at import) cannot appear in results anyway.
            skipped += 1
        else:
            add(("anilist", media_id), row)

    store_exclusion_list(profiles.list_name(LIST_NAME), entries)
    status = get_status(profiles.list_name(LIST_NAME))
    status.skipped = skipped
    return status


@router.get("/yamtrack", response_model=ExclusionStatus)
def yamtrack_status() -> ExclusionStatus:
    return get_status(profiles.list_name(LIST_NAME))
