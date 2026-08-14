"""MAL integration via Jikan: cache a user's anime + manga lists locally.

Everything on either list (any status) can then be excluded from
recommendation and random results by passing mal_user=<username>.
"""

import httpx
from fastapi import APIRouter, HTTPException

from api.config import settings
from api.db import get_pool
from api.models import UserListsStatus, UserListStatus
from pipeline.client import RateLimitedClient

router = APIRouter()

JIKAN_BASE = "https://api.jikan.moe/v4"
LIST_TYPES = ("anime", "manga")

INSERT_ENTRY_SQL = """
    INSERT INTO mal_list_entries (username, list_type, id_mal, status, score)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (username, list_type, id_mal)
    DO UPDATE SET status = EXCLUDED.status, score = EXCLUDED.score
"""

UPSERT_STATE_SQL = """
    INSERT INTO mal_list_state (username, list_type, entry_count, fetched_at)
    VALUES (%s, %s, %s, now())
    ON CONFLICT (username, list_type)
    DO UPDATE SET entry_count = EXCLUDED.entry_count, fetched_at = now()
"""


def _fetch_list(
    client: RateLimitedClient, username: str, list_type: str
) -> dict[int, tuple[str, int | None]]:
    """Page through a Jikan user list and return {mal_id: (status, score)}.

    MAL user scores are 1-10 (0 = unrated), stored normalized to 0-100 with
    unrated as NULL so those entries keep a neutral For-you sampling weight.
    """
    entries: dict[int, tuple[str, int | None]] = {}
    page = 1
    while True:
        try:
            data = client.request(
                "GET",
                f"{JIKAN_BASE}/users/{username}/{list_type}list",
                params={"page": page},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(
                    status_code=404, detail=f"MAL user '{username}' not found"
                ) from exc
            raise HTTPException(
                status_code=502, detail=f"Jikan error {exc.response.status_code}"
            ) from exc
        except httpx.TransportError as exc:
            raise HTTPException(status_code=502, detail="Jikan unreachable") from exc

        for item in data.get("data", []):
            node = item.get("anime") or item.get("manga") or {}
            mal_id = node.get("mal_id")
            if mal_id:
                status = item.get("watching_status") or item.get("reading_status") or ""
                raw_score = item.get("score")
                score = int(raw_score) * 10 if isinstance(raw_score, (int, float)) else 0
                entries[int(mal_id)] = (str(status), score if score else None)
        if not data.get("pagination", {}).get("has_next_page"):
            break
        page += 1
    return entries


@router.post("/mal/{username}/refresh", response_model=UserListsStatus)
def refresh_mal_lists(username: str) -> UserListsStatus:
    username = username.strip().lower()
    if not username:
        raise HTTPException(status_code=422, detail="empty username")
    client = RateLimitedClient(min_interval=settings.jikan_min_interval)
    try:
        lists = {lt: _fetch_list(client, username, lt) for lt in LIST_TYPES}
    finally:
        client.close()

    with get_pool().connection() as conn:
        for list_type, entries in lists.items():
            conn.execute(
                "DELETE FROM mal_list_entries WHERE username = %s AND list_type = %s",
                (username, list_type),
            )
            if entries:
                with conn.cursor() as cur:
                    cur.executemany(
                        INSERT_ENTRY_SQL,
                        [
                            (username, list_type, mal_id, st, score)
                            for mal_id, (st, score) in entries.items()
                        ],
                    )
            conn.execute(UPSERT_STATE_SQL, (username, list_type, len(entries)))
    return get_mal_status(username)


@router.get("/mal/{username}", response_model=UserListsStatus)
def get_mal_status(username: str) -> UserListsStatus:
    username = username.strip().lower()
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT list_type, entry_count, fetched_at FROM mal_list_state"
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
