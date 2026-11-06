"""Hardcover integration (https://hardcover.app), the book tracker.

Pulls the token owner's library from Hardcover's GraphQL API (me ->
user_books) and stores it as the "hardcover" generic exclusion list, so
books on it are left out of results and feed My taste and For you like
the other lists. Exclude with exclude_list=hardcover.

Hardcover names books by its own ids, with ISBNs on their editions; the
catalogue holds Open Library works. Each library entry's ISBNs (the
edition the reader chose first, then the book's other editions) are
resolved to a work through Open Library's /isbn endpoint, and the work to
the book's media.id. An entry with no ISBN Open Library knows, or whose
work is not in the catalogue, is counted as skipped.

Status: 1 Want to Read counts as planned (so keep_planned spares it);
2 Reading, 3 Read, 4 Paused, 5 Did Not Finish and 6 Ignored are excluded
outright. A rating (0-5 in halves) is scaled to 0-100.

The token is a personal access token from hardcover.app > Settings > API,
stored write-only like Yamtrack's, with HARDCOVER_TOKEN as the fallback.
Hardcover's terms let a self-hosted, personal install use the owner's own
library; they do not allow a public or commercial site to use user data.
"""

import httpx
from fastapi import APIRouter, HTTPException

from api.config import settings
from api.models import ExclusionStatus
from api.openlibrary_match import book_ids_by_work, isbn_work_key, openlibrary_client
from api.routers.app_settings import effective_hardcover_token
from api.routers.exclusions import get_status, store_exclusion_list
from pipeline.client import RateLimitedClient

router = APIRouter()

LIST_NAME = "hardcover"
HARDCOVER = "https://api.hardcover.app/v1/graphql"
PAGE = 100
# ISBNs tried per entry before giving up on it. Most resolve on the first.
MAX_ISBNS = 6
WANT_TO_READ = 1

# One top-level field (me), so one request against the rate limit. Only
# what matching needs: no ratings counts, reader counts or anything else
# popularity-shaped is requested.
LIBRARY_QUERY = """
query Library($limit: Int!, $offset: Int!) {
  me {
    user_books(limit: $limit, offset: $offset, order_by: {id: asc}) {
      status_id
      rating
      edition { isbn_13 isbn_10 }
      book {
        editions(limit: 10, order_by: {id: asc}) { isbn_13 isbn_10 }
      }
    }
  }
}
"""


def _user_books(data: dict) -> list[dict]:
    if data.get("errors"):
        first = data["errors"][0]
        message = first.get("message") if isinstance(first, dict) else str(first)
        raise HTTPException(status_code=502, detail=f"Hardcover refused the query: {message}")
    me = (data.get("data") or {}).get("me")
    # me is a list of one (the token's owner) in Hardcover's schema.
    if isinstance(me, list):
        me = me[0] if me else {}
    return (me or {}).get("user_books") or []


def fetch_library(client: RateLimitedClient) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        data = client.request(
            "POST",
            HARDCOVER,
            json_body={"query": LIBRARY_QUERY, "variables": {"limit": PAGE, "offset": offset}},
        )
        page = _user_books(data)
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def isbns_of(entry: dict) -> list[str]:
    """The entry's own edition first, then the book's other editions."""
    editions = [entry.get("edition") or {}]
    editions += ((entry.get("book") or {}).get("editions")) or []
    out: list[str] = []
    for edition in editions:
        for field in ("isbn_13", "isbn_10"):
            isbn = str((edition or {}).get(field) or "").replace("-", "").strip()
            if isbn and isbn not in out:
                out.append(isbn)
    return out[:MAX_ISBNS]


def _score(raw) -> int | None:
    """0-5 in halves (Hasura's numeric may arrive as a string) to 0-100."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return int(round(value * 20)) if value > 0 else None


def work_of(entry: dict, client: RateLimitedClient) -> str | None:
    for isbn in isbns_of(entry):
        work = isbn_work_key(isbn, client)
        if work is not None:
            return work
    return None


@router.post("/hardcover/refresh", response_model=ExclusionStatus)
def refresh_hardcover() -> ExclusionStatus:
    token = effective_hardcover_token()
    if not token:
        raise HTTPException(status_code=422, detail="add a Hardcover API token in settings first")
    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"
    hardcover = RateLimitedClient(
        # 60 requests a minute on Hardcover's free plan.
        min_interval=1.1,
        max_retries=settings.interactive_max_retries,
        timeout=settings.interactive_timeout,
        extra_headers={"Authorization": token},
    )
    openlibrary = openlibrary_client()
    resolved: list[tuple[str, dict]] = []
    skipped = 0
    try:
        try:
            library = fetch_library(hardcover)
        except httpx.HTTPStatusError as exc:
            detail = f"Hardcover error {exc.response.status_code}"
            if exc.response.status_code in (401, 403):
                detail = "Hardcover rejected the token (it may have expired; make a new one)"
            raise HTTPException(status_code=502, detail=detail) from exc
        except httpx.TransportError as exc:
            raise HTTPException(status_code=502, detail="Hardcover unreachable") from exc
        for entry in library:
            work = work_of(entry, openlibrary)
            if work is None:
                skipped += 1
            else:
                resolved.append((work, entry))
    finally:
        hardcover.close()
        openlibrary.close()

    by_work = book_ids_by_work(sorted({work for work, _ in resolved}))
    entries: dict[tuple[str, int], tuple[bool, int | None]] = {}
    for work, entry in resolved:
        media_id = by_work.get(work)
        if media_id is None:
            # Not in the catalogue (not imported, or below the description
            # gate), so it could never appear in results anyway.
            skipped += 1
            continue
        planned = entry.get("status_id") == WANT_TO_READ
        score = _score(entry.get("rating"))
        key = ("anilist", media_id)
        prev_planned, prev_score = entries.get(key, (True, None))
        scores = [s for s in (prev_score, score) if s is not None]
        entries[key] = (prev_planned and planned, max(scores) if scores else None)

    store_exclusion_list(LIST_NAME, entries)
    status = get_status(LIST_NAME)
    status.skipped = skipped
    return status


@router.get("/hardcover", response_model=ExclusionStatus)
def hardcover_status() -> ExclusionStatus:
    return get_status(LIST_NAME)
