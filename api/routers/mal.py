"""MAL integration via Jikan: cache a user's anime + manga lists locally.

Everything on either list (any status) can then be excluded from
recommendation and random results by passing mal_user=<username>.

KNOWN BROKEN, and not for the reason it looks like. A refresh returns
{"detail": "Jikan unreachable"}, which is true - api.jikan.moe refuses both
GitHub's runners (ConnectError Errno 101, re-measured 2026-11-04) and the
deployment host - but it masks something that reachability would not fix.
Jikan's own OpenAPI spec marks exactly two endpoints deprecated out of its
entire v4 surface, and they are the two below:

    GET /users/{username}/animelist   deprecated
    GET /users/{username}/mangalist   deprecated

with the description "User Anime lists have been discontinued since May 1st,
2022". Their documented response schema is empty. So this cannot work even
from a host Jikan answers, and chasing the transport error - container DNS,
egress rules, the proxy - would have found nothing.

Two consequences worth stating plainly rather than leaving to be rediscovered:

  - The `page` parameter this module sends is not in the spec for these
    endpoints either (only `status` is), so the pagination loop below was
    never going to page anything.
  - CLAUDE.md still specifies Jikan user endpoints for list retrieval
    "without OAuth". That was true when it was written and is not now.

What still works, and what the UI should prefer: AniList
(api/routers/anilist.py) and Kitsu (api/routers/kitsu.py) both read lists
from reachable, non-deprecated APIs and feed the same exclusion machinery.
The exclusion itself is sound and tested - only this one source's fetch is
dead.

The real alternative for MAL specifically is the official MAL API v2
(api.myanimelist.net/v2/users/{user}/animelist), which serves public lists
with a registered X-MAL-CLIENT-ID header and no OAuth user flow. That is a
new integration, not a URL swap, so it is left unbuilt rather than guessed
at - and it is untestable from here while api.jikan.moe and friends are
unreachable.

Everything else MAL-shaped in this project is unaffected: id_mal joins,
/search/by-mal, and the dub probe's /anime/{id}/characters (not deprecated)
all stand.
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

# Shown to the user instead of a bare transport error. See the module
# docstring: Jikan's spec marks these two endpoints, and only these two,
# deprecated - "discontinued since May 1st, 2022" - so reachability is not
# the fix and the message must not imply it is.
DISCONTINUED = (
    "MAL list sync is unavailable: Jikan discontinued its user list endpoints"
    " in 2022 and api.jikan.moe does not answer this host. Use the AniList or"
    " Kitsu list instead - both feed the same exclusion."
)

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
                status_code=502,
                detail=f"Jikan error {exc.response.status_code}. {DISCONTINUED}",
            ) from exc
        except httpx.TransportError as exc:
            # "Jikan unreachable" alone was true and actively misleading: it
            # sent an investigation after container DNS and egress rules when
            # the endpoint is discontinued and would fail from a reachable
            # host too. An error message that names only the nearest cause
            # costs more than it saves.
            raise HTTPException(status_code=502, detail=DISCONTINUED) from exc

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
    client = RateLimitedClient(
        min_interval=settings.jikan_min_interval,
        max_retries=settings.interactive_max_retries,
        timeout=settings.interactive_timeout,
    )
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
