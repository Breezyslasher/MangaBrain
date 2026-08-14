"""AniList list integration: cache a user's anime + manga lists locally.

Unlike the MAL path (which joins on id_mal), exclusion here is a direct match
on the AniList media id. The whole list arrives in a few chunked
MediaListCollection queries with no API key. Everything on either list (any
status) can then be excluded by passing anilist_user=<username>.
"""

import httpx
from fastapi import APIRouter, HTTPException

from api.config import settings
from api.db import get_pool
from api.models import UserListsStatus, UserListStatus
from pipeline.client import RateLimitedClient

router = APIRouter()

ANILIST_URL = "https://graphql.anilist.co"
LIST_TYPES = {"anime": "ANIME", "manga": "MANGA"}
PER_CHUNK = 500

LIST_QUERY = """
query ($userName: String!, $type: MediaType!, $chunk: Int!, $perChunk: Int!) {
  MediaListCollection(userName: $userName, type: $type, chunk: $chunk, perChunk: $perChunk) {
    hasNextChunk
    lists { entries { status media { id } } }
  }
}
"""

INSERT_ENTRY_SQL = """
    INSERT INTO anilist_list_entries (username, list_type, media_id, status)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (username, list_type, media_id) DO UPDATE SET status = EXCLUDED.status
"""

UPSERT_STATE_SQL = """
    INSERT INTO anilist_list_state (username, list_type, entry_count, fetched_at)
    VALUES (%s, %s, %s, now())
    ON CONFLICT (username, list_type)
    DO UPDATE SET entry_count = EXCLUDED.entry_count, fetched_at = now()
"""


def _fetch_list(client: RateLimitedClient, username: str, media_type: str) -> dict[int, str]:
    """Chunk through a user's MediaListCollection and return {media_id: status}."""
    entries: dict[int, str] = {}
    chunk = 1
    while True:
        try:
            payload = client.request(
                "POST",
                ANILIST_URL,
                json_body={
                    "query": LIST_QUERY,
                    "variables": {
                        "userName": username,
                        "type": media_type,
                        "chunk": chunk,
                        "perChunk": PER_CHUNK,
                    },
                },
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(
                    status_code=404, detail=f"AniList user '{username}' not found"
                ) from exc
            raise HTTPException(
                status_code=502, detail=f"AniList error {exc.response.status_code}"
            ) from exc
        except httpx.TransportError as exc:
            raise HTTPException(status_code=502, detail="AniList unreachable") from exc

        if payload.get("errors"):
            messages = "; ".join(e.get("message", "") for e in payload["errors"])
            if "not found" in messages.lower():
                raise HTTPException(status_code=404, detail=f"AniList user '{username}' not found")
            raise HTTPException(status_code=502, detail=f"AniList error: {messages}")

        collection = (payload.get("data") or {}).get("MediaListCollection")
        if collection is None:
            break
        for lst in collection.get("lists") or []:
            for entry in lst.get("entries") or []:
                media = entry.get("media") or {}
                if media.get("id"):
                    entries[int(media["id"])] = str(entry.get("status") or "")
        if not collection.get("hasNextChunk"):
            break
        chunk += 1
    return entries


@router.post("/anilist/{username}/refresh", response_model=UserListsStatus)
def refresh_anilist_lists(username: str) -> UserListsStatus:
    username = username.strip()
    if not username:
        raise HTTPException(status_code=422, detail="empty username")
    client = RateLimitedClient(min_interval=settings.anilist_min_interval)
    try:
        lists = {lt: _fetch_list(client, username, mt) for lt, mt in LIST_TYPES.items()}
    finally:
        client.close()

    stored = username.lower()
    with get_pool().connection() as conn:
        for list_type, entries in lists.items():
            conn.execute(
                "DELETE FROM anilist_list_entries WHERE username = %s AND list_type = %s",
                (stored, list_type),
            )
            if entries:
                with conn.cursor() as cur:
                    cur.executemany(
                        INSERT_ENTRY_SQL,
                        [(stored, list_type, mid, st) for mid, st in entries.items()],
                    )
            conn.execute(UPSERT_STATE_SQL, (stored, list_type, len(entries)))
    return get_anilist_status(stored)


@router.get("/anilist/{username}", response_model=UserListsStatus)
def get_anilist_status(username: str) -> UserListsStatus:
    username = username.strip().lower()
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT list_type, entry_count, fetched_at FROM anilist_list_state"
            " WHERE username = %s ORDER BY list_type",
            (username,),
        ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="no cached lists; refresh first")
    return UserListsStatus(
        username=username,
        lists=[
            UserListStatus(
                list_type=row["list_type"],
                entry_count=row["entry_count"],
                fetched_at=row["fetched_at"],
            )
            for row in rows
        ],
    )
