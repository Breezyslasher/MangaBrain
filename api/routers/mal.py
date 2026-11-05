"""MAL integration: cache a user's anime + manga lists locally.

Everything on either list (any status) can then be excluded from
recommendation and random results by passing mal_user=<username>.

This reads the OFFICIAL MyAnimeList API, not Jikan. Jikan marks exactly
two endpoints deprecated in its whole v4 surface - /users/{u}/animelist
and /users/{u}/mangalist - with the description "User Anime lists have
been discontinued since May 1st, 2022", and their documented response
schema is empty. No amount of reachability fixes that, which is why the
transport error this module used to return sent an investigation after
container DNS and egress rules for nothing.

    GET https://api.myanimelist.net/v2/users/{username}/{type}list
        ?fields=list_status&limit=1000&nsfw=true
        X-MAL-CLIENT-ID: <client id>

A public list needs only a registered client id - no OAuth, no user
login. Yamtrack reads the same lists the same way, from the same host
that Jikan refuses, which is how this route was found: the user asked why
their tracker could do what this could not.

Two differences from the Jikan shape that are easy to miss:

  - Status is a string ("plan_to_watch", "completed"), where Jikan sent
    numeric codes ("6" for plan-to-watch). api/filters.py's keep_planned
    guard matches both, so a list cached under either shape still honours
    the toggle.
  - Paging follows paging.next, an absolute URL, rather than a page
    number. The old loop sent ?page=N, which was not in Jikan's spec for
    these endpoints either and never paged anything.

Dub data is NOT here. The official API has no characters or voice-actor
resource at all; that lives on Jikan's /anime/{id}/characters, which is a
different service and is not deprecated.
"""

import httpx
from fastapi import APIRouter, HTTPException

from api.config import settings
from api.db import get_pool
from api.models import UserListsStatus, UserListStatus
from pipeline.client import RateLimitedClient

router = APIRouter()

MAL_BASE = "https://api.myanimelist.net/v2"
LIST_TYPES = ("anime", "manga")

# nsfw=true because this list is used to EXCLUDE what the user has already
# seen: omitting adult entries would quietly recommend them back.
LIST_PARAMS = {"fields": "list_status", "limit": 1000, "nsfw": "true"}

NO_CLIENT_ID = (
    "MAL list sync needs a client id. Create one at"
    " https://myanimelist.net/apiconfig (free, no review) and set"
    " MAL_CLIENT_ID on the api service. AniList and Kitsu need no credential"
    " and feed the same exclusion."
)

UNREACHABLE = (
    "api.myanimelist.net did not answer. The list is unchanged; try again, or"
    " use the AniList or Kitsu list, which feed the same exclusion."
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
    """Page through a MAL user list and return {mal_id: (status, score)}.

    MAL user scores are 1-10 (0 = unrated), stored normalized to 0-100 with
    unrated as NULL so those entries keep a neutral For-you sampling weight.
    """
    entries: dict[int, tuple[str, int | None]] = {}
    url: str | None = f"{MAL_BASE}/users/{username}/{list_type}list"
    params: dict | None = dict(LIST_PARAMS)
    while url:
        try:
            data = client.request("GET", url, params=params)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code == 404:
                raise HTTPException(
                    status_code=404,
                    detail=f"MAL user '{username}' not found, or their list is private",
                ) from exc
            if code in (401, 403):
                raise HTTPException(
                    status_code=502,
                    detail=f"MAL rejected the client id (HTTP {code}). {NO_CLIENT_ID}",
                ) from exc
            raise HTTPException(status_code=502, detail=f"MAL error {code}. {UNREACHABLE}") from exc
        except httpx.TransportError as exc:
            raise HTTPException(status_code=502, detail=UNREACHABLE) from exc

        for item in data.get("data", []):
            node = item.get("node") or {}
            mal_id = node.get("id")
            if not mal_id:
                continue
            status = (item.get("list_status") or {}).get("status") or ""
            raw_score = (item.get("list_status") or {}).get("score")
            score = int(raw_score) * 10 if isinstance(raw_score, (int, float)) else 0
            entries[int(mal_id)] = (str(status), score if score else None)

        # paging.next is an absolute URL with the offset already in it, so
        # re-sending params would fight with it.
        url = (data.get("paging") or {}).get("next")
        params = None
    return entries


@router.post("/mal/{username}/refresh", response_model=UserListsStatus)
def refresh_mal_lists(username: str) -> UserListsStatus:
    username = username.strip().lower()
    if not username:
        raise HTTPException(status_code=422, detail="empty username")
    if not settings.mal_client_id:
        # Refused before the request, so the error names the missing
        # credential instead of whatever the API says to an unauthenticated
        # caller.
        raise HTTPException(status_code=422, detail=NO_CLIENT_ID)
    client = RateLimitedClient(
        min_interval=settings.mal_min_interval,
        max_retries=settings.interactive_max_retries,
        timeout=settings.interactive_timeout,
        extra_headers={"X-MAL-CLIENT-ID": settings.mal_client_id},
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
