"""The genre and tag vocabulary of each medium, for the filter panels.

The clients used to show AniList's eighteen genres on every tab and offer
tag suggestions from the whole catalogue, so a TV or books search was
offered "Mahou Shoujo" and "TI-83 (Calculator)". Each medium names its
genres differently - TVmaze has "Legal" and "Medical", TMDB "Western",
books "Biography" - and only a medium's own words can filter it.

Both lists come from the catalogue itself, per medium group:
  - genres: every genre a non-adult title in the group carries;
  - tags: the MAX_TAGS tag names carried by the most non-adult titles in
    the group, alphabetical. Open Library subjects run to hundreds of
    thousands of one-off headings, too many to send and no use as a
    filter; how many titles share a word says how useful it is as a
    filter, nothing about any title's popularity, and it never reaches
    ranking.

Counting tags across a million books takes seconds to tens of seconds on a
NAS, longer than a client waits, so the lists are computed in the
background when the API starts, cached for a day, and served stale while a
refresh runs.
"""

import threading
import time
from collections.abc import Callable

from api.db import get_pool
from api.media_groups import ALL_MEDIUMS, MEDIUM_GROUPS

TTL = 24 * 3600.0
MAX_TAGS = 3000

GENRES_SQL = """
    SELECT DISTINCT g AS name
    FROM media m, unnest(m.genres) g
    WHERE m.medium = ANY(%(mediums)s) AND m.is_adult IS NOT TRUE AND g <> ''
    ORDER BY name
"""

TAGS_SQL = """
    SELECT name FROM (
        SELECT t->>'name' AS name, count(*) AS titles
        FROM media m, jsonb_array_elements(m.tags) t
        WHERE m.medium = ANY(%(mediums)s) AND m.is_adult IS NOT TRUE
          AND coalesce(t->>'name', '') <> ''
          AND coalesce(t->>'is_adult', 'false') <> 'true'
        GROUP BY 1
        ORDER BY titles DESC, name
        LIMIT %(limit)s
    ) top
    ORDER BY name
"""

KINDS = {"genres": GENRES_SQL, "tags": TAGS_SQL}

_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}
_locks: dict[tuple[str, str], threading.Lock] = {}
_locks_guard = threading.Lock()


def mediums_for(group: str | None) -> list[str]:
    return MEDIUM_GROUPS[group] if group else ALL_MEDIUMS


def compute(kind: str, group: str | None) -> list[str]:
    with get_pool().connection() as conn:
        rows = conn.execute(
            KINDS[kind], {"mediums": mediums_for(group), "limit": MAX_TAGS}
        ).fetchall()
    return [row["name"] for row in rows]


def _lock(key: tuple[str, str]) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _refresh(key: tuple[str, str], kind: str, group: str | None, lock: threading.Lock) -> None:
    try:
        _cache[key] = (time.monotonic(), compute(kind, group))
    except Exception:  # noqa: BLE001 - the stale list stays; the next call retries
        pass
    finally:
        lock.release()


def vocabulary(
    kind: str, group: str | None, now: Callable[[], float] = time.monotonic
) -> list[str]:
    """The cached list; computed here only when there is nothing to serve."""
    key = (kind, group or "")
    entry = _cache.get(key)
    if entry is not None:
        lock = _lock(key)
        if now() - entry[0] > TTL and lock.acquire(blocking=False):
            threading.Thread(target=_refresh, args=(key, kind, group, lock), daemon=True).start()
        return entry[1]
    # Nothing yet: wait for a warm-up already computing it, or compute it.
    with _lock(key):
        entry = _cache.get(key)
        if entry is None:
            entry = (now(), compute(kind, group))
            _cache[key] = entry
    return entry[1]


def warm() -> None:
    """Fill every medium's lists, genres first (cheap), in the background."""

    def run() -> None:
        for kind in ("genres", "tags"):
            for group in MEDIUM_GROUPS:
                try:
                    vocabulary(kind, group)
                except Exception:  # noqa: BLE001 - a request computes it instead
                    pass

    threading.Thread(target=run, name="vocabulary-warm", daemon=True).start()


def forget() -> None:
    _cache.clear()
