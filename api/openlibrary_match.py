"""Matching other services' books onto the Open Library catalogue.

The books catalogue holds Open Library works ("/works/OL45804W"), stored
as media rows with source 'openlibrary'. Trackers name books differently:
Yamtrack by an Open Library edition id, Hardcover by ISBN. Both resolve
to a work through Open Library's public API, and the work to the media.id
the exclusion lists store (under kind "anilist", the kind that matches
media.id directly whatever the row's source).
"""

import re

import httpx

from api.config import settings
from api.db import get_pool
from pipeline.client import RateLimitedClient

OPENLIBRARY = "https://openlibrary.org"
# Open Library ids: OL<digits> then M (edition), W (work) or A (author).
OL_ID = re.compile(r"^OL\d+([MW])$")
ISBN = re.compile(r"^(\d{9}[\dX]|\d{13})$")


def openlibrary_client() -> RateLimitedClient:
    """Paced for a public API, cached on disk so a re-sync costs nothing."""
    return RateLimitedClient(
        min_interval=0.35,
        cache_dir=settings.http_cache_dir,
        max_retries=settings.interactive_max_retries,
        timeout=settings.interactive_timeout,
    )


def _edition_work(client: RateLimitedClient, path: str) -> str | None:
    """The work of the edition at `path` ("/books/OL1M" or "/isbn/...")."""
    # One hop of redirect: a merged edition answers with a redirect record
    # naming the edition that survived. (/isbn/ itself is an HTTP redirect,
    # which the client follows.)
    edition: dict = {}
    for _ in range(2):
        try:
            edition = client.request("GET", f"{OPENLIBRARY}{path}.json")
        except (httpx.HTTPStatusError, httpx.TransportError, RuntimeError):
            return None
        if (edition.get("type") or {}).get("key") != "/type/redirect":
            break
        path = str(edition.get("location") or "")
        if not path.startswith("/books/"):
            return None
    works = edition.get("works") or []
    key = (works[0] or {}).get("key") if works else None
    return key if isinstance(key, str) and key.startswith("/works/") else None


def openlibrary_work_key(media_id: str, client: RateLimitedClient) -> str | None:
    """The work key for an Open Library edition or work id.

    Yamtrack's book search stores the top edition, so the id is usually an
    edition and needs one request to find its work; a work id passes
    straight through. None for anything unresolvable, which callers count
    as skipped rather than failing a whole sync.
    """
    media_id = media_id.strip()
    match = OL_ID.match(media_id)
    if not match:
        return None
    if match.group(1) == "W":
        return f"/works/{media_id}"
    return _edition_work(client, f"/books/{media_id}")


def isbn_work_key(isbn: str, client: RateLimitedClient) -> str | None:
    """The work key for an ISBN-10 or ISBN-13, or None."""
    isbn = isbn.replace("-", "").strip().upper()
    if not ISBN.match(isbn):
        return None
    return _edition_work(client, f"/isbn/{isbn}")


def book_ids_by_work(work_keys: list[str]) -> dict[str, int]:
    """media.id for each work key the books catalogue holds."""
    if not work_keys:
        return {}
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT source_id, id FROM media WHERE source = 'openlibrary' AND source_id = ANY(%s)",
            (work_keys,),
        ).fetchall()
    # The API pool hands out dict rows (api.db), so a row is read by
    # name. Unpacking one as a tuple yields its two KEYS, which is how
    # the first version of this matched no book at all.
    return {row["source_id"]: row["id"] for row in rows}
