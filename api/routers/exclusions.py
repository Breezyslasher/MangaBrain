"""Generic named exclusion lists.

Any tracker or script can push a list of AniList ids and/or typed MAL ids
(anime and manga are separate MAL id spaces), then exclude everything on it
from recommendations, random picks, and the personal feed by passing
exclude_list=<name>. Posting replaces the whole list, so exports stay simple:
dump ids, POST, done.
"""

import re

from fastapi import APIRouter, HTTPException

from api.db import get_pool
from api.models import ExclusionListIn, ExclusionStatus

router = APIRouter()

NAME_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
MAX_ENTRIES = 200_000

INSERT_SQL = """
    INSERT INTO custom_exclusion_entries (list_name, kind, ext_id, planned, score)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT DO NOTHING
"""

UPSERT_STATE_SQL = """
    INSERT INTO custom_exclusion_state (list_name, entry_count, updated_at)
    VALUES (%s, %s, now())
    ON CONFLICT (list_name)
    DO UPDATE SET entry_count = EXCLUDED.entry_count, updated_at = now()
"""


def normalize_name(name: str) -> str:
    name = name.strip().lower()
    if not NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="list name must be 1-64 chars of a-z, 0-9, hyphen, underscore",
        )
    return name


def store_exclusion_list(
    name: str, entries: dict[tuple[str, int], tuple[bool, int | None]]
) -> None:
    """Replace the named list with {(kind, ext_id): (planned, score)} entries.

    planned=True marks a plan-to-watch/plan-to-read tracker entry, which the
    keep_planned query option can skip; started/finished entries are False.
    score is the user's rating normalized to 0-100 (None = unrated), used to
    weight For-you seed sampling.
    """
    if len(entries) > MAX_ENTRIES:
        raise HTTPException(status_code=413, detail=f"list exceeds {MAX_ENTRIES} entries")
    with get_pool().connection() as conn:
        conn.execute("DELETE FROM custom_exclusion_entries WHERE list_name = %s", (name,))
        if entries:
            with conn.cursor() as cur:
                cur.executemany(
                    INSERT_SQL,
                    [
                        (name, kind, ext_id, planned, score)
                        for (kind, ext_id), (planned, score) in entries.items()
                    ],
                )
        conn.execute(UPSERT_STATE_SQL, (name, len(entries)))


def get_status(name: str) -> ExclusionStatus:
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT list_name, entry_count, updated_at FROM custom_exclusion_state"
            " WHERE list_name = %s",
            (name,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no exclusion list named '{name}'")
    return ExclusionStatus(
        name=row["list_name"], entry_count=row["entry_count"], updated_at=row["updated_at"]
    )


@router.post("/exclusions/{name}", response_model=ExclusionStatus)
def replace_exclusion_list(name: str, body: ExclusionListIn) -> ExclusionStatus:
    name = normalize_name(name)
    # Manually posted ids carry no tracker status or rating, so they always
    # exclude (planned=False) and sample with neutral weight (score=None).
    entries: dict[tuple[str, int], tuple[bool, int | None]] = {}
    entries.update({("anilist", i): (False, None) for i in body.anilist_ids})
    entries.update({("mal_anime", i): (False, None) for i in body.mal_anime_ids})
    entries.update({("mal_manga", i): (False, None) for i in body.mal_manga_ids})
    store_exclusion_list(name, entries)
    return get_status(name)


@router.get("/exclusions/{name}", response_model=ExclusionStatus)
def exclusion_list_status(name: str) -> ExclusionStatus:
    return get_status(normalize_name(name))


@router.delete("/exclusions/{name}", response_model=ExclusionStatus)
def delete_exclusion_list(name: str) -> ExclusionStatus:
    name = normalize_name(name)
    status = get_status(name)
    with get_pool().connection() as conn:
        conn.execute("DELETE FROM custom_exclusion_entries WHERE list_name = %s", (name,))
        conn.execute("DELETE FROM custom_exclusion_state WHERE list_name = %s", (name,))
    return status
