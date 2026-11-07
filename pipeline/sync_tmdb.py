"""Films from TMDB (https://www.themoviedb.org), and the TV shows TVmaze
lacks.

TV's first source is TVmaze (pipeline.sync_tvmaze), whose CC BY-SA licence
plainly allows what this engine does with the data. TMDB is its second:
a show is kept from TMDB only when no TVmaze show matches it by IMDb id,
TheTVDB id, or title, premiere year and country together. The survey put
TVmaze at about 51,000 usable shows and TMDB at about 80,000, so TMDB
adds at most some 30,000, fewer where the two overlap.

On TMDB's terms, read 6 Nov 2026 (API terms last updated 20 Oct 2023):
1.C forbids using TMDB content "in connection with, including for
training, a machine learning (ML) or artificial intelligence (AI) based
Application" and making "derivatives" of it. TMDB staff have answered
on its forum that a non-commercial similarity feature is not what that
clause is meant to carve out, and that semantic search is not training
AI. This module is built to stay inside that reading: each install
fetches with its own key, nothing is published or shared, nothing is
trained, embeddings are only compared for similarity, data is refreshed
inside the six-month cache limit, and TMDB is credited. The staff
answers are guidance, not a written agreement; an operator who wants
certainty should ask TMDB about their own install.

Fetched by each install with its own API key and kept in its own
database - never published as a dataset. That is the shape TMDB's terms
allow: non-commercial use with attribution, data cached no longer than
six months, and no redistribution. So there is no GitHub release for this
medium; the worker fetches it, and refreshes it on a rolling schedule.

Three passes:

  full     every id in TMDB's daily film and TV id exports (adult and
           video entries dropped), each fetched with its keywords and
           external ids. Resumable: progress is kept in sync_state per
           type. About 1.4 million requests, ten hours or so at the rate
           below.
  changes  the ids TMDB reports changed since the last pass (its /changes
           feeds), re-fetched. Run nightly.
  refresh  rows older than REFRESH_DAYS re-fetched, oldest first, a slice
           per night - the six-month cache limit, kept with room to spare.

What is kept (measured by pipeline.survey_screen, 6 Nov 2026): a title
needs an overview of at least MIN_OVERVIEW characters, which about 43% of
films and 36% of shows have. Japanese animation is dropped: the anime
catalogue already holds it, from AniList, with far better tags.

What is stored maps onto the media table the anime rows use: overview as
the description, TMDB genres as genres, keywords as tags (TMDB does not
rank them, so every keyword gets one rank), the year, origin country,
a status in the catalogue's vocabulary, and the poster. TMDB's
popularity, vote average and vote count are never read: nothing here
may rank by fame, and a filter on any of them would be one.

    python -m pipeline.sync_tmdb full          # resumable
    python -m pipeline.sync_tmdb changes
    python -m pipeline.sync_tmdb refresh --limit 6000
    python -m pipeline.sync_tmdb tvmaze-keywords --limit 100000

Needs a TMDB API key (themoviedb.org > Settings > API): a v3 key or a v4
read access token, from the settings page or TMDB_API_KEY.
"""

import argparse
import gzip
import json
import re
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import httpx
import psycopg
from psycopg.types.json import Jsonb

from api.config import settings

SOURCE = "tmdb"
API = "https://api.themoviedb.org/3"
EXPORTS = "https://files.tmdb.org/p/exports"
IMAGES = "https://image.tmdb.org/t/p"

MIN_OVERVIEW = 200
MAX_DESCRIPTION = 4000
KEYWORD_RANK = 50
REFRESH_DAYS = 150
# TMDB allows about 50 requests a second per IP; stay under it. Requests
# are made from several threads because a single connection's latency,
# not the limit, would otherwise set the pace.
REQUESTS_PER_SECOND = 35
WORKERS = 8
CHUNK = 400

# (TMDB type, path in the API, export file prefix, media_type, medium)
KINDS = {
    "movie": ("movie", "movie", "MOVIE", "movie"),
    "tv": ("tv", "tv_series", "TV", "tv"),
}

# TMDB status words onto the catalogue's AniList-derived vocabulary, so the
# one status filter means the same thing on every tab.
STATUS = {
    "Released": "FINISHED",
    "Ended": "FINISHED",
    "Returning Series": "RELEASING",
    "In Production": "NOT_YET_RELEASED",
    "Post Production": "NOT_YET_RELEASED",
    "Planned": "NOT_YET_RELEASED",
    "Rumored": "NOT_YET_RELEASED",
    "Pilot": "NOT_YET_RELEASED",
    "Canceled": "CANCELLED",
}

STATE_FULL = "tmdb_full_{kind}"
STATE_CHANGES = "tmdb_changes"

WHITESPACE = re.compile(r"\s+")


# ------------------------------------------------------------------ client


class Gone(Exception):
    """TMDB answered 404: the id was deleted or merged."""


class TmdbClient:
    """Thread-safe, paced client. A v4 token goes in a header, a v3 key in
    the query string."""

    def __init__(self, key: str, rate: float = REQUESTS_PER_SECOND, max_retries: int = 5):
        key = key.strip()
        headers = {"User-Agent": "MangaBrain/0.1 (self-hosted recommendation engine)"}
        self.params: dict[str, str] = {}
        if key.count(".") == 2:
            headers["Authorization"] = f"Bearer {key}"
        else:
            self.params["api_key"] = key
        self.http = httpx.Client(timeout=30.0, headers=headers, follow_redirects=True)
        self.interval = 1.0 / rate
        self.next_at = 0.0
        self.lock = threading.Lock()
        self.max_retries = max_retries

    def _wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + self.interval
        if at > now:
            time.sleep(at - now)

    def get(self, path: str, params: dict | None = None) -> dict:
        backoff = 2.0
        for attempt in range(self.max_retries + 1):
            self._wait()
            try:
                resp = self.http.get(f"{API}{path}", params={**self.params, **(params or {})})
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
                time.sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code == 404:
                raise Gone(path)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.max_retries:
                    resp.raise_for_status()
                retry = resp.headers.get("Retry-After", "")
                time.sleep(float(retry) if retry.isdigit() else backoff)
                backoff *= 2
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("retries exhausted")

    def close(self) -> None:
        self.http.close()


# ------------------------------------------------------------------ rows


def clean(text: str | None) -> str:
    return WHITESPACE.sub(" ", text or "").strip()


def year_of(value: str | None) -> int | None:
    match = re.match(r"(\d{4})", value or "")
    return int(match.group(1)) if match else None


def is_japanese_animation(data: dict) -> bool:
    genres = {g.get("name") for g in data.get("genres") or []}
    origin = set(data.get("origin_country") or []) | {
        c.get("iso_3166_1") for c in data.get("production_countries") or []
    }
    return "Animation" in genres and ("JP" in origin or data.get("original_language") == "ja")


def row_for(kind: str, data: dict) -> dict | None:
    """A media row, or None when the title cannot be ranked or is kept out."""
    if data.get("adult") or data.get("video"):
        return None
    overview = clean(data.get("overview"))
    if len(overview) < MIN_OVERVIEW or is_japanese_animation(data):
        return None
    if kind == "movie":
        title = clean(data.get("title"))
        original = clean(data.get("original_title"))
        released = data.get("release_date")
        fmt = "MOVIE"
        episodes = None
    else:
        title = clean(data.get("name"))
        original = clean(data.get("original_name"))
        released = data.get("first_air_date")
        fmt = (clean(data.get("type")) or "Scripted").upper().replace(" ", "_")
        episodes = data.get("number_of_episodes")
    if not title:
        return None
    keywords = data.get("keywords") or {}
    keywords = keywords.get("keywords") or keywords.get("results") or []
    tags = []
    for keyword in keywords:
        name = clean(keyword.get("name"))
        if name and name.lower() not in {t["name"].lower() for t in tags}:
            tags.append({"name": name, "rank": KEYWORD_RANK, "is_adult": False})
    origin = list(data.get("origin_country") or []) or [
        c.get("iso_3166_1") for c in data.get("production_countries") or []
    ]
    poster = data.get("poster_path")
    ids = data.get("external_ids") or {}
    external_ids = {}
    if ids.get("imdb_id"):
        external_ids["imdb"] = str(ids["imdb_id"])
    if ids.get("tvdb_id"):
        external_ids["tvdb"] = str(ids["tvdb_id"])
    media_type, medium = KINDS[kind][2], KINDS[kind][3]
    return {
        "source": SOURCE,
        "source_id": f"{kind}/{int(data['id'])}",
        "media_type": media_type,
        "medium": medium,
        "title": title,
        "title_native": original if original and original != title else None,
        "description": overview[:MAX_DESCRIPTION],
        "genres": [clean(g.get("name")) for g in data.get("genres") or [] if g.get("name")],
        "tags": tags,
        "format": fmt,
        "episodes": episodes if isinstance(episodes, int) else None,
        "country": origin[0] if origin and origin[0] else None,
        "start_year": year_of(released),
        "status": STATUS.get(clean(data.get("status"))),
        "cover_medium": f"{IMAGES}/w342{poster}" if poster else None,
        "cover_large": f"{IMAGES}/w780{poster}" if poster else None,
        "synonyms": [original] if original and original != title else [],
        "external_ids": external_ids,
    }


# ------------------------------------------------------------------ store

UPDATE_SQL = """
    UPDATE media SET
        media_type = %(media_type)s, medium = %(medium)s,
        title_romaji = %(title)s, title_english = %(title)s, title_native = %(title_native)s,
        description = %(description)s, description_clean = %(description)s,
        genres = %(genres)s, tags = %(tags)s, format = %(format)s, episodes = %(episodes)s,
        country_of_origin = %(country)s, start_year = %(start_year)s, status = %(status)s,
        cover_image_medium = %(cover_medium)s, cover_image_large = %(cover_large)s,
        synonyms = %(synonyms)s, external_ids = %(external_ids)s, is_adult = FALSE,
        synced_at = now()
    WHERE source = %(source)s AND source_id = %(source_id)s
"""

# Only for rows the UPDATE did not find: nextval in an upsert's VALUES
# spends an id per row whether it inserts or not, and the id column is
# 32-bit.
INSERT_SQL = """
    INSERT INTO media (
        id, source, source_id, media_type, medium,
        title_romaji, title_english, title_native, description, description_clean,
        genres, tags, format, episodes, country_of_origin, start_year, status,
        cover_image_medium, cover_image_large, synonyms, external_ids, is_adult, synced_at
    ) VALUES (
        nextval('media_external_id_seq'), %(source)s, %(source_id)s, %(media_type)s, %(medium)s,
        %(title)s, %(title)s, %(title_native)s, %(description)s, %(description)s,
        %(genres)s, %(tags)s, %(format)s, %(episodes)s, %(country)s, %(start_year)s, %(status)s,
        %(cover_medium)s, %(cover_large)s, %(synonyms)s, %(external_ids)s, FALSE, now()
    )
    ON CONFLICT (source, source_id) DO NOTHING
"""

DELETE_SQL = "DELETE FROM media WHERE source = %s AND source_id = %s"


# A TMDB show that TVmaze already holds: same IMDb or TheTVDB id, or the
# same title, premiere year and country. TVmaze is TV's first source.
TVMAZE_HAS_SQL = """
    SELECT 1 FROM media v
    WHERE v.source = 'tvmaze' AND (
        (%(imdb)s::text IS NOT NULL AND v.external_ids->>'imdb' = %(imdb)s)
        OR (%(tvdb)s::text IS NOT NULL AND v.external_ids->>'tvdb' = %(tvdb)s)
        OR (lower(v.title_english) = lower(%(title)s)
            AND v.start_year IS NOT DISTINCT FROM %(start_year)s::int
            AND v.country_of_origin IS NOT DISTINCT FROM %(country)s::text)
    )
    LIMIT 1
"""


def tvmaze_has(conn: psycopg.Connection, row: dict) -> bool:
    ids = row.get("external_ids") or {}
    params = {
        "imdb": ids.get("imdb"),
        "tvdb": ids.get("tvdb"),
        "title": row["title"],
        "start_year": row["start_year"],
        "country": row["country"],
    }
    return conn.execute(TVMAZE_HAS_SQL, params).fetchone() is not None


def store(conn: psycopg.Connection, kind: str, tmdb_id: int, row: dict | None) -> str:
    """Write one title; returns 'kept', 'dropped', 'removed' or 'duplicate'."""
    if row is not None and kind == "tv" and tvmaze_has(conn, row):
        conn.execute(DELETE_SQL, (SOURCE, f"{kind}/{tmdb_id}"))
        return "duplicate"
    if row is None:
        # A title that no longer qualifies (overview removed, marked adult,
        # deleted) must not linger: drop any row it had.
        deleted = conn.execute(DELETE_SQL, (SOURCE, f"{kind}/{tmdb_id}")).rowcount
        return "removed" if deleted else "dropped"
    params = {**row, "tags": Jsonb(row["tags"]), "external_ids": Jsonb(row["external_ids"])}
    if conn.execute(UPDATE_SQL, params).rowcount == 0:
        conn.execute(INSERT_SQL, params)
    return "kept"


def fetch(client: TmdbClient, kind: str, tmdb_id: int) -> dict | None:
    """The title's row, or None if it is gone or does not qualify."""
    path = KINDS[kind][0]
    try:
        data = client.get(
            f"/{path}/{tmdb_id}",
            {"append_to_response": "keywords,external_ids", "language": "en-US"},
        )
    except Gone:
        return None
    return row_for(kind, data)


def process(
    conn: psycopg.Connection,
    client: TmdbClient,
    kind: str,
    ids: list[int],
    tally: dict[str, int],
) -> None:
    """Fetch ids concurrently, store them in order, commit once."""
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        rows = list(pool.map(lambda i: fetch(client, kind, i), ids))
    for tmdb_id, row in zip(ids, rows, strict=True):
        outcome = store(conn, kind, tmdb_id, row)
        tally[outcome] = tally.get(outcome, 0) + 1
    conn.commit()


# ------------------------------------------------------------------ state


def get_state(conn: psycopg.Connection, key: str) -> dict:
    row = conn.execute("SELECT value FROM sync_state WHERE key = %s", (key,)).fetchone()
    if row is None:
        return {}
    value = row[0]
    return value if isinstance(value, dict) else json.loads(value)


def set_state(conn: psycopg.Connection, key: str, value: dict) -> None:
    conn.execute(
        "INSERT INTO sync_state (key, value, updated_at) VALUES (%s, %s, now())"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        (key, Jsonb(value)),
    )


def stored_key(conn: psycopg.Connection) -> str:
    """The settings page's key first, then TMDB_API_KEY."""
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'tmdb_api_key'").fetchone()
    return (row[0] if row else "") or settings.tmdb_api_key


# ------------------------------------------------------------------ passes


def export_ids(kind: str) -> tuple[str, list[int]]:
    """(export date, ids) from the newest daily export, adult and video out."""
    prefix = KINDS[kind][1]
    for back in range(4):
        day = datetime.now(UTC).date() - timedelta(days=back)
        url = f"{EXPORTS}/{prefix}_ids_{day:%m_%d_%Y}.json.gz"
        resp = httpx.get(url, timeout=300, follow_redirects=True)
        if resp.status_code != 200:
            continue
        ids = []
        for line in gzip.decompress(resp.content).splitlines():
            entry = json.loads(line)
            if not entry.get("adult") and not entry.get("video"):
                ids.append(int(entry["id"]))
        return day.isoformat(), sorted(ids)
    raise RuntimeError(f"no TMDB {prefix} export in the last four days")


def full(
    conn: psycopg.Connection,
    client: TmdbClient,
    kinds: Iterable[str] = ("movie", "tv"),
    progress: Callable[[dict], None] | None = None,
) -> dict[str, int]:
    """Every exported id, once, resumable. Ids are processed in ascending
    order and the last one done is recorded, so a restart picks up after
    it even when a newer export has been published meanwhile. After the
    first complete pass, the changes feed keeps titles current and new
    ids arrive through it, so a kind already complete is skipped."""
    tally: dict[str, int] = {}
    plans = []
    for kind in kinds:
        version, ids = export_ids(kind)
        state = get_state(conn, STATE_FULL.format(kind=kind))
        if state.get("complete"):
            continue
        after = state.get("after", 0)
        plans.append((kind, version, [i for i in ids if i > after]))
    total = sum(len(todo) for _, _, todo in plans)
    done = 0
    # Said at once, so the settings page shows the size of the job rather
    # than "Starting" until the first 400 titles are in.
    if progress is not None:
        progress({"phase": "fetching", "bytes": 0, "total": total})
    started = time.monotonic()
    for kind, version, todo in plans:
        key = STATE_FULL.format(kind=kind)
        for start in range(0, len(todo), CHUNK):
            chunk = todo[start : start + CHUNK]
            process(conn, client, kind, chunk, tally)
            set_state(conn, key, {"export": version, "after": chunk[-1], "complete": False})
            conn.commit()
            done += len(chunk)
            rate = done / max(time.monotonic() - started, 1e-9)
            print(
                f"[tmdb] {done:,}/{total:,} ({100 * done / max(total, 1):.1f}%)"
                f"  kept {tally.get('kept', 0):,}  {rate:.0f}/s"
                f"  ETA {(total - done) / max(rate, 1e-9) / 60:.0f}m",
                flush=True,
            )
            if progress is not None:
                progress({"phase": "fetching", "bytes": done, "total": total})
        set_state(conn, key, {"export": version, "after": 0, "complete": True})
        conn.commit()
    return tally


def changed_ids(client: TmdbClient, kind: str, start: date, end: date) -> list[int]:
    """Ids TMDB reports changed in [start, end]; its window is 14 days."""
    path = KINDS[kind][0]
    ids: set[int] = set()
    page = 1
    while True:
        data = client.get(
            f"/{path}/changes",
            {"start_date": start.isoformat(), "end_date": end.isoformat(), "page": page},
        )
        for entry in data.get("results") or []:
            if not entry.get("adult"):
                ids.add(int(entry["id"]))
        if page >= int(data.get("total_pages") or 1):
            return sorted(ids)
        page += 1


def changes(conn: psycopg.Connection, client: TmdbClient) -> dict[str, int]:
    """Re-fetch what changed since the last pass, up to 14 days back."""
    tally: dict[str, int] = {}
    today = datetime.now(UTC).date()
    state = get_state(conn, STATE_CHANGES)
    since = date.fromisoformat(state["through"]) if state.get("through") else today
    start = max(since - timedelta(days=1), today - timedelta(days=13))
    for kind in KINDS:
        ids = changed_ids(client, kind, start, today)
        for i in range(0, len(ids), CHUNK):
            process(conn, client, kind, ids[i : i + CHUNK], tally)
    set_state(conn, STATE_CHANGES, {"through": today.isoformat()})
    conn.commit()
    return tally


def refresh(conn: psycopg.Connection, client: TmdbClient, limit: int) -> dict[str, int]:
    """Re-fetch the oldest rows past REFRESH_DAYS, so none is kept longer
    than TMDB's six-month cache limit."""
    tally: dict[str, int] = {}
    rows = conn.execute(
        "SELECT source_id FROM media WHERE source = %s"
        " AND synced_at < now() - make_interval(days => %s)"
        " ORDER BY synced_at LIMIT %s",
        (SOURCE, REFRESH_DAYS, limit),
    ).fetchall()
    by_kind: dict[str, list[int]] = {}
    for (source_id,) in rows:
        kind, _, raw = str(source_id).partition("/")
        if kind in KINDS and raw.isdigit():
            by_kind.setdefault(kind, []).append(int(raw))
    for kind, ids in by_kind.items():
        for i in range(0, len(ids), CHUNK):
            process(conn, client, kind, ids[i : i + CHUNK], tally)
    return tally


# ------------------------------------------------------- TVmaze keywords

# TVmaze has genres but no keywords, so its ~48,000 shows had empty tags
# while TMDB's own shows carried keywords: a tag filter on the TV tab only
# ever matched TMDB's third. This pass finds each TVmaze show on TMDB by
# its IMDb id, else its TheTVDB id (TMDB's /find), and puts that show's
# keywords in the TVmaze row's tags. No match by id, no keywords: a title
# search would guess. Re-checked every REFRESH_DAYS like every other TMDB
# row, which keeps the six-month cache limit.
#
# The keywords are TMDB's, so they stay on this install. The TV dataset
# (pipeline.tv_dataset) publishes TVmaze's rows under CC BY-SA; its export
# refuses a database whose TVmaze rows carry tags, and its load never
# overwrites them.

KEYWORDS_DUE_SQL = """
    SELECT id, external_ids->>'imdb', external_ids->>'tvdb', tmdb_match_id, tags
    FROM media
    WHERE source = 'tvmaze'
      AND (tmdb_keywords_at IS NULL
           OR tmdb_keywords_at < now() - make_interval(days => %s))
    ORDER BY tmdb_keywords_at NULLS FIRST, id
    LIMIT %s
"""

KEYWORDS_SET_SQL = """
    UPDATE media SET tags = %(tags)s, tmdb_match_id = %(match)s, tmdb_keywords_at = now()
    WHERE id = %(id)s
"""

# Tags are part of the embedded text, so a show whose keywords changed is
# embedded again by the next pass.
STALE_EMBEDDING_SQL = "DELETE FROM embeddings WHERE media_id = %s"


def find_tv(client: TmdbClient, imdb: str | None, tvdb: str | None) -> int | None:
    """The TMDB TV id for these outside ids, or None."""
    for value, source in ((imdb, "imdb_id"), (tvdb, "tvdb_id")):
        if not value:
            continue
        try:
            data = client.get(f"/find/{value}", {"external_source": source})
        except Gone:
            continue
        results = data.get("tv_results") or []
        if results and results[0].get("id"):
            return int(results[0]["id"])
    return None


def keyword_tags(data: dict) -> list[dict]:
    """TMDB's keywords reply as tags, in the shape row_for gives them."""
    found = data.get("results") or data.get("keywords") or []
    tags: list[dict] = []
    for keyword in found:
        name = clean(keyword.get("name"))
        if name and name.lower() not in {t["name"].lower() for t in tags}:
            tags.append({"name": name, "rank": KEYWORD_RANK, "is_adult": False})
    return tags


def lookup_keywords(client: TmdbClient, imdb, tvdb, known: int | None):
    """(TMDB id, tags) for one TVmaze show; (None, []) when none matches."""
    match = known or find_tv(client, imdb, tvdb)
    if match is None:
        return None, []
    try:
        return match, keyword_tags(client.get(f"/tv/{match}/keywords"))
    except Gone:
        # The remembered show was deleted or merged; look it up afresh.
        if known is not None:
            return lookup_keywords(client, imdb, tvdb, None)
        return None, []


def tvmaze_keywords(
    conn: psycopg.Connection,
    client: TmdbClient,
    limit: int,
    progress: Callable[[dict], None] | None = None,
) -> dict[str, int]:
    """Put TMDB's keywords on the TVmaze shows due a look. Returns counts."""
    rows = conn.execute(KEYWORDS_DUE_SQL, (REFRESH_DAYS, limit)).fetchall()
    tally = {"checked": 0, "matched": 0, "changed": 0}
    started = time.monotonic()
    for start in range(0, len(rows), CHUNK):
        chunk = rows[start : start + CHUNK]
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            found = list(pool.map(lambda r: lookup_keywords(client, r[1], r[2], r[3]), chunk))
        for (media_id, _imdb, _tvdb, _known, old), (match, tags) in zip(chunk, found, strict=True):
            tally["checked"] += 1
            if match is not None:
                tally["matched"] += 1
            if tags != (old or []):
                tally["changed"] += 1
                conn.execute(STALE_EMBEDDING_SQL, (media_id,))
            conn.execute(KEYWORDS_SET_SQL, {"id": media_id, "match": match, "tags": Jsonb(tags)})
        conn.commit()
        done = tally["checked"]
        rate = done / max(time.monotonic() - started, 1e-9)
        print(
            f"[tmdb] TVmaze keywords {done:,}/{len(rows):,}  matched {tally['matched']:,}"
            f"  {rate:.0f}/s  ETA {(len(rows) - done) / max(rate, 1e-9) / 60:.0f}m",
            flush=True,
        )
        if progress is not None:
            progress({"phase": "fetching", "bytes": done, "total": len(rows)})
    return tally


def has_titles(conn: psycopg.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM media WHERE source = %s LIMIT 1", (SOURCE,)).fetchone()
        is not None
    )


def nightly(refresh_limit: int = 6000, keywords_limit: int = 100_000) -> None:
    """The worker's pass for an install that holds TMDB titles. Never
    raises: a TMDB outage must not cost the AniList sync."""
    try:
        with psycopg.connect(settings.database_url) as conn:
            if not has_titles(conn):
                return
            key = stored_key(conn)
            if not key:
                print("[tmdb] no API key configured; films and TV are not being refreshed")
                return
            client = TmdbClient(key)
            try:
                print(f"[tmdb] changes: {changes(conn, client)}", flush=True)
                print(f"[tmdb] refresh: {refresh(conn, client, refresh_limit)}", flush=True)
                # The first night does every TVmaze show (about 45 minutes);
                # after that only new shows and ones due a refresh.
                print(
                    f"[tmdb] TVmaze keywords: {tvmaze_keywords(conn, client, keywords_limit)}",
                    flush=True,
                )
            finally:
                client.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[tmdb] nightly pass failed ({type(exc).__name__}: {exc})", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync films and TV from TMDB")
    parser.add_argument("mode", choices=["full", "changes", "refresh", "tvmaze-keywords"])
    parser.add_argument("--kind", choices=list(KINDS), action="append")
    parser.add_argument(
        "--limit", type=int, default=6000, help="rows per refresh or tvmaze-keywords pass"
    )
    args = parser.parse_args()
    from api.db import ensure_schema

    ensure_schema()
    with psycopg.connect(settings.database_url) as conn:
        key = stored_key(conn)
        if not key:
            raise SystemExit("Set a TMDB API key in Settings, or TMDB_API_KEY.")
        client = TmdbClient(key)
        try:
            if args.mode == "full":
                tally = full(conn, client, args.kind or list(KINDS))
            elif args.mode == "changes":
                tally = changes(conn, client)
            elif args.mode == "tvmaze-keywords":
                tally = tvmaze_keywords(conn, client, args.limit)
            else:
                tally = refresh(conn, client, args.limit)
        finally:
            client.close()
    print(f"[tmdb] done: {tally}")


if __name__ == "__main__":
    main()
