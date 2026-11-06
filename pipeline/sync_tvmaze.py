"""TV shows from TVmaze (https://www.tvmaze.com).

TVmaze's API is licensed CC BY-SA: "the data can freely be used for any
purpose, as long as TVmaze is properly credited as source and your usage
complies with the ShareAlike provision" (tvmaze.com/api, read 6 Nov
2026). So storing it, embedding it and republishing it are all allowed,
with a link back to TVmaze as the credit. That is why TV comes from here
and not TMDB, whose terms rule out machine-learning use.

The show index (/shows?page=N) returns up to 250 shows per page with
everything this needs - name, summary, genres, type, status, premiere,
country, image - ordered by id, and a 404 past the last page. The whole
catalogue is a few hundred requests, minutes at TVmaze's limit of 20
calls per 10 seconds, so there is no incremental pass: every run reads
all of it, which also lets a run remove shows TVmaze has deleted or that
no longer qualify.

What is kept (measured by pipeline.survey_screen, 6 Nov 2026: 56% of
~95,500 shows): a summary of at least MIN_SUMMARY characters once its
HTML is stripped. Japanese animation is dropped; the anime catalogue
already holds it, from AniList, with far better tags. TVmaze has genres
but no keywords, so the synopsis carries the ranking. Its rating and
"weight" (a popularity measure) are never read.

    python -m pipeline.sync_tvmaze
"""

import html
import re
import time
from collections.abc import Callable

import httpx
import psycopg

from api.config import settings

SOURCE = "tvmaze"
API = "https://api.tvmaze.com"
MIN_SUMMARY = 200
MAX_DESCRIPTION = 4000
# Below this many shows read, an index is treated as truncated, and
# nothing is removed. The real index was about 95,500 on 6 Nov 2026.
MIN_INDEX = 50_000
# 20 calls per 10 seconds is TVmaze's documented floor.
INTERVAL = 0.55

STATUS = {
    "Running": "RELEASING",
    "Ended": "FINISHED",
    "To Be Determined": "HIATUS",
    "In Development": "NOT_YET_RELEASED",
}

TAG = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"\s+")


def clean(text: str | None) -> str:
    return WHITESPACE.sub(" ", html.unescape(TAG.sub(" ", text or ""))).strip()


def country_of(show: dict) -> str | None:
    for channel in ("network", "webChannel"):
        code = (((show.get(channel) or {}).get("country")) or {}).get("code")
        if code:
            return code
    return None


def is_japanese_animation(show: dict) -> bool:
    return (show.get("type") == "Animation" or "Anime" in (show.get("genres") or [])) and (
        country_of(show) == "JP" or show.get("language") == "Japanese"
    )


def row_for(show: dict) -> dict | None:
    """A media row, or None when the show cannot be ranked or is kept out."""
    title = clean(show.get("name"))
    summary = clean(show.get("summary"))
    if not title or len(summary) < MIN_SUMMARY or is_japanese_animation(show):
        return None
    year = re.match(r"(\d{4})", show.get("premiered") or "")
    image = show.get("image") or {}
    kind = clean(show.get("type")) or "Scripted"
    return {
        "source": SOURCE,
        "source_id": str(int(show["id"])),
        "title": title,
        "description": summary[:MAX_DESCRIPTION],
        "genres": [g for g in show.get("genres") or [] if g],
        "format": kind.upper().replace(" ", "_"),
        "country": country_of(show),
        "start_year": int(year.group(1)) if year else None,
        "status": STATUS.get(show.get("status") or ""),
        "cover_medium": image.get("medium"),
        "cover_large": image.get("original") or image.get("medium"),
    }


UPDATE_SQL = """
    UPDATE media SET
        title_romaji = %(title)s, title_english = %(title)s,
        description = %(description)s, description_clean = %(description)s,
        genres = %(genres)s, format = %(format)s, country_of_origin = %(country)s,
        start_year = %(start_year)s, status = %(status)s,
        cover_image_medium = %(cover_medium)s, cover_image_large = %(cover_large)s,
        synced_at = now()
    WHERE source = %(source)s AND source_id = %(source_id)s
"""

# Only for shows the UPDATE did not find, so no id is drawn per existing
# row (nextval in an upsert's VALUES would spend one each time).
INSERT_SQL = """
    INSERT INTO media (
        id, source, source_id, media_type, medium,
        title_romaji, title_english, description, description_clean,
        genres, tags, format, country_of_origin, start_year, status,
        cover_image_medium, cover_image_large, synced_at
    ) VALUES (
        nextval('media_external_id_seq'), %(source)s, %(source_id)s, 'TV', 'tv',
        %(title)s, %(title)s, %(description)s, %(description)s,
        %(genres)s, '[]', %(format)s, %(country)s, %(start_year)s, %(status)s,
        %(cover_medium)s, %(cover_large)s, now()
    )
    ON CONFLICT (source, source_id) DO NOTHING
"""


def store(conn: psycopg.Connection, row: dict) -> None:
    if conn.execute(UPDATE_SQL, row).rowcount == 0:
        conn.execute(INSERT_SQL, row)


def pages(client: httpx.Client):
    """Each index page's shows, in order, until TVmaze answers 404."""
    page = 0
    backoff = 2.0
    while True:
        started = time.monotonic()
        resp = client.get(f"{API}/shows", params={"page": page})
        if resp.status_code == 404:
            return
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
            continue
        resp.raise_for_status()
        backoff = 2.0
        yield page, resp.json()
        page += 1
        time.sleep(max(0.0, INTERVAL - (time.monotonic() - started)))


def sync(progress: Callable[[dict], None] | None = None) -> dict[str, int]:
    """Read the whole index; store what qualifies, remove what no longer
    does. Returns counts."""
    tally = {"read": 0, "kept": 0}
    seen: list[str] = []
    headers = {"User-Agent": "MangaBrain/0.1 (self-hosted recommendation engine)"}
    with (
        httpx.Client(timeout=60.0, headers=headers, follow_redirects=True) as client,
        psycopg.connect(settings.database_url) as conn,
    ):
        for page, shows in pages(client):
            for show in shows:
                tally["read"] += 1
                row = row_for(show)
                if row is None:
                    continue
                store(conn, row)
                seen.append(row["source_id"])
                tally["kept"] += 1
            conn.commit()
            if page % 20 == 0:
                print(
                    f"[tvmaze] page {page}: {tally['read']:,} read, {tally['kept']:,} kept",
                    flush=True,
                )
            if progress is not None:
                progress({"phase": "fetching", "bytes": tally["read"]})
        # Only after a complete pass - pages() returns on the 404 that
        # ends the index, and raises on anything else - so a run cut short
        # never deletes shows it simply had not reached. And never on an
        # index that came back implausibly small: a TVmaze outage that
        # answered 404 early must not empty the TV tab.
        removed = 0
        if tally["read"] >= MIN_INDEX:
            removed = conn.execute(
                "DELETE FROM media WHERE source = %s AND NOT (source_id = ANY(%s))",
                (SOURCE, seen),
            ).rowcount
            conn.commit()
    tally["removed"] = removed
    print(f"[tvmaze] done: {tally}", flush=True)
    return tally


def has_shows(conn: psycopg.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM media WHERE source = %s LIMIT 1", (SOURCE,)).fetchone()
        is not None
    )


def nightly() -> None:
    """The worker's pass for an install that holds TV shows. A full read
    is a few minutes, so it is the whole upkeep. Never raises."""
    try:
        with psycopg.connect(settings.database_url) as conn:
            if not has_shows(conn):
                return
        sync()
    except Exception as exc:  # noqa: BLE001 - must not cost the AniList sync
        print(f"[tvmaze] nightly pass failed ({type(exc).__name__}: {exc})", flush=True)


def main() -> None:
    from api.db import ensure_schema

    ensure_schema()
    sync()


if __name__ == "__main__":
    main()
