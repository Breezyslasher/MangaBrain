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


# One entry as stored: (planned, score).
EntryState = tuple[bool, "int | None"]


def merge_entry(
    entries: dict[tuple[str, int], EntryState],
    key: tuple[str, int],
    planned: bool,
    score: int | None,
) -> None:
    """Fold one (kind, ext_id) entry into the accumulator.

    The same external id can arrive several times: from two Kitsu mappings,
    from a client that sends both a bare id and a detailed entry, or from a
    title tracked on two of a user's lists. Started always wins over planned
    (so a title being read is never left recommendable), and the higher of
    the two ratings is kept. Shared by every list source so they cannot
    drift apart.
    """
    prev_planned, prev_score = entries.get(key, (True, None))
    scores = [s for s in (prev_score, score) if s is not None]
    entries[key] = (prev_planned and planned, max(scores) if scores else None)


def normalize_name(name: str) -> str:
    name = name.strip().lower()
    if not NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="list name must be 1-64 chars of a-z, 0-9, hyphen, underscore",
        )
    return name


def store_exclusion_list(name: str, entries: dict[tuple[str, int], EntryState]) -> None:
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
    entries: dict[tuple[str, int], EntryState] = {}
    # Bare ids carry no tracker status or rating: they always exclude
    # (planned=False) and sample with neutral weight (score=None).
    for kind, ids in (
        ("anilist", body.anilist_ids),
        ("mal_anime", body.mal_anime_ids),
        ("mal_manga", body.mal_manga_ids),
    ):
        for ext_id in ids:
            merge_entry(entries, (kind, ext_id), False, None)

    # Kitsu ids are not a join key for the exclusion filter, so they are
    # translated to the MAL/AniList ids they map to before storing. Imported
    # here rather than at module scope: kitsu.py imports this module.
    kitsu_items = [e for e in body.entries if e.kind.startswith("kitsu_")]
    kitsu_keys: dict[tuple[str, int], list[tuple[str, int]]] = {}
    if kitsu_items:
        from api.routers.kitsu import external_keys_for

        wanted: dict[str, list[int]] = {}
        for item in kitsu_items:
            wanted.setdefault(item.kind.removeprefix("kitsu_"), []).append(item.id)
        kitsu_keys = external_keys_for(wanted)

    skipped = 0
    for item in body.entries:
        if item.kind.startswith("kitsu_"):
            targets = kitsu_keys.get((item.kind, item.id)) or []
            if not targets:
                # No usable mapping on Kitsu's side, so nothing to exclude.
                skipped += 1
            for key in targets:
                merge_entry(entries, key, item.planned, item.score)
        else:
            merge_entry(entries, (item.kind, item.id), item.planned, item.score)

    store_exclusion_list(name, entries)
    status = get_status(name)
    if kitsu_items:
        status.skipped = skipped
    return status


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
