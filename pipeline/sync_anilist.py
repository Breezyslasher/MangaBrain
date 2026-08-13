"""Sync the AniList catalog into the local media table.

Full sync cursors by id (id_greater) because AniList caps pagination offsets
at 5000 rows; the cursor is checkpointed in sync_state after every committed
batch, so an interrupted run picks up where it left off. Incremental sync
walks UPDATED_AT_DESC and stops at the first entry older than the given
cutoff (or at the offset cap, with a warning).

Usage:
    python -m pipeline.sync_anilist --type anime
    python -m pipeline.sync_anilist --type manga
    python -m pipeline.sync_anilist --type anime --since 1700000000
"""

import argparse
import json
import time
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

from api.config import settings
from pipeline.clean import clean_description
from pipeline.client import RateLimitedClient
from pipeline.medium import derive_medium

ANILIST_URL = "https://graphql.anilist.co"
PER_PAGE = 50

# AniList rejects any paginated query whose offset (page * perPage) exceeds
# 5000 rows with a 400, so the full sync cannot walk page numbers. It cursors
# by id instead (id_greater, always page 1), which has no depth limit. The
# incremental sync sorts by UPDATED_AT_DESC, which has no id cursor, so it is
# capped at the offset limit and warns if it hits it.
MAX_OFFSET_PAGES = 5000 // PER_PAGE

MEDIA_FIELDS = """
      id
      idMal
      type
      format
      title { romaji english native }
      description(asHtml: false)
      genres
      tags { name rank isAdult }
      episodes
      chapters
      volumes
      countryOfOrigin
      startDate { year }
      status
      averageScore
      popularity
      favourites
      isAdult
      updatedAt
      coverImage { medium large }
      relations { edges { relationType node { id } } }
"""

BY_ID_QUERY = f"""
query ($type: MediaType!, $idGreater: Int!) {{
  Page(page: 1, perPage: {PER_PAGE}) {{
    pageInfo {{ hasNextPage }}
    media(type: $type, sort: ID, id_greater: $idGreater) {{
{MEDIA_FIELDS}
    }}
  }}
}}
"""

BY_UPDATED_QUERY = f"""
query ($page: Int!, $type: MediaType!) {{
  Page(page: $page, perPage: {PER_PAGE}) {{
    pageInfo {{ hasNextPage }}
    media(type: $type, sort: UPDATED_AT_DESC) {{
{MEDIA_FIELDS}
    }}
  }}
}}
"""

UPSERT_SQL = """
    INSERT INTO media (
        id, id_mal, media_type, medium, title_romaji, title_english, title_native,
        description, description_clean, genres, tags, format, episodes, chapters,
        volumes, country_of_origin, start_year, status, average_score,
        cover_image_medium, cover_image_large, is_adult, popularity, favourites,
        updated_at, synced_at
    ) VALUES (
        %(id)s, %(id_mal)s, %(media_type)s, %(medium)s, %(title_romaji)s,
        %(title_english)s, %(title_native)s, %(description)s, %(description_clean)s,
        %(genres)s, %(tags)s, %(format)s, %(episodes)s, %(chapters)s, %(volumes)s,
        %(country_of_origin)s, %(start_year)s, %(status)s, %(average_score)s,
        %(cover_image_medium)s, %(cover_image_large)s, %(is_adult)s, %(popularity)s,
        %(favourites)s, %(updated_at)s, now()
    )
    ON CONFLICT (id) DO UPDATE SET
        id_mal = EXCLUDED.id_mal,
        media_type = EXCLUDED.media_type,
        medium = EXCLUDED.medium,
        title_romaji = EXCLUDED.title_romaji,
        title_english = EXCLUDED.title_english,
        title_native = EXCLUDED.title_native,
        description = EXCLUDED.description,
        description_clean = EXCLUDED.description_clean,
        genres = EXCLUDED.genres,
        tags = EXCLUDED.tags,
        format = EXCLUDED.format,
        episodes = EXCLUDED.episodes,
        chapters = EXCLUDED.chapters,
        volumes = EXCLUDED.volumes,
        country_of_origin = EXCLUDED.country_of_origin,
        start_year = EXCLUDED.start_year,
        status = EXCLUDED.status,
        average_score = EXCLUDED.average_score,
        cover_image_medium = EXCLUDED.cover_image_medium,
        cover_image_large = EXCLUDED.cover_image_large,
        is_adult = EXCLUDED.is_adult,
        popularity = EXCLUDED.popularity,
        favourites = EXCLUDED.favourites,
        updated_at = EXCLUDED.updated_at,
        synced_at = now()
"""


def get_state(conn: psycopg.Connection, key: str) -> dict | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = %s", (key,)).fetchone()
    if row is None:
        return None
    value = row[0] if not isinstance(row, dict) else row["value"]
    return value if isinstance(value, dict) else json.loads(value)


def set_state(conn: psycopg.Connection, key: str, value: dict) -> None:
    conn.execute(
        "INSERT INTO sync_state (key, value, updated_at) VALUES (%s, %s, now())"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        (key, Jsonb(value)),
    )


def clear_state(conn: psycopg.Connection, key: str) -> None:
    conn.execute("DELETE FROM sync_state WHERE key = %s", (key,))


def fetch_page(client: RateLimitedClient, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = client.request(
            "POST", ANILIST_URL, json_body={"query": query, "variables": variables}
        )
    except httpx.HTTPStatusError as exc:
        # Surface AniList's GraphQL error body, not just the status code.
        raise RuntimeError(
            f"AniList HTTP {exc.response.status_code}: {exc.response.text[:500]}"
        ) from exc
    if payload.get("errors"):
        raise RuntimeError(f"AniList error: {payload['errors']}")
    return payload["data"]["Page"]


def upsert_entry(conn: psycopg.Connection, entry: dict[str, Any]) -> None:
    title = entry.get("title") or {}
    cover = entry.get("coverImage") or {}
    start = entry.get("startDate") or {}
    description = entry.get("description") or ""
    tags = [
        {"name": t.get("name"), "rank": t.get("rank"), "is_adult": t.get("isAdult", False)}
        for t in (entry.get("tags") or [])
        if t.get("name")
    ]
    conn.execute(
        UPSERT_SQL,
        {
            "id": entry["id"],
            "id_mal": entry.get("idMal"),
            "media_type": entry["type"],
            "medium": derive_medium(
                entry["type"], entry.get("format"), entry.get("countryOfOrigin")
            ),
            "title_romaji": title.get("romaji"),
            "title_english": title.get("english"),
            "title_native": title.get("native"),
            "description": description,
            "description_clean": clean_description(description),
            "genres": entry.get("genres") or [],
            "tags": Jsonb(tags),
            "format": entry.get("format"),
            "episodes": entry.get("episodes"),
            "chapters": entry.get("chapters"),
            "volumes": entry.get("volumes"),
            "country_of_origin": entry.get("countryOfOrigin"),
            "start_year": start.get("year"),
            "status": entry.get("status"),
            "average_score": entry.get("averageScore"),
            "cover_image_medium": cover.get("medium"),
            "cover_image_large": cover.get("large"),
            "is_adult": entry.get("isAdult") or False,
            "popularity": entry.get("popularity"),
            "favourites": entry.get("favourites"),
            "updated_at": entry.get("updatedAt"),
        },
    )
    conn.execute("DELETE FROM media_relations WHERE media_id = %s", (entry["id"],))
    edges = (entry.get("relations") or {}).get("edges") or []
    rows = [
        (entry["id"], edge["node"]["id"], edge.get("relationType") or "UNKNOWN")
        for edge in edges
        if edge.get("node")
    ]
    if rows:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO media_relations (media_id, related_id, relation_type)"
                " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                rows,
            )


def full_sync(client: RateLimitedClient, conn: psycopg.Connection, media_type: str) -> None:
    checkpoint_key = f"anilist_full_{media_type.lower()}"
    state = get_state(conn, checkpoint_key) or {}
    last_id = int(state.get("last_id", 0))
    started = int(time.time())
    total = 0
    if last_id:
        print(f"[sync] resuming {media_type} full sync after id {last_id}")
    while True:
        data = fetch_page(client, BY_ID_QUERY, {"type": media_type, "idGreater": last_id})
        entries = data["media"]
        for entry in entries:
            upsert_entry(conn, entry)
        if entries:
            last_id = entries[-1]["id"]
        total += len(entries)
        set_state(conn, checkpoint_key, {"last_id": last_id})
        conn.commit()
        print(f"[sync] {media_type} {total} entries this run (cursor at id {last_id})")
        if not entries or not data["pageInfo"]["hasNextPage"]:
            break
    clear_state(conn, checkpoint_key)
    set_state(conn, f"anilist_last_sync_{media_type.lower()}", {"ts": started})
    conn.commit()
    print(f"[sync] {media_type} full sync complete")


def incremental_sync(
    client: RateLimitedClient, conn: psycopg.Connection, media_type: str, since: int
) -> int:
    """Sync entries updated since the given unix timestamp. Returns the count."""
    page = 1
    updated = 0
    while True:
        data = fetch_page(client, BY_UPDATED_QUERY, {"page": page, "type": media_type})
        reached_cutoff = False
        for entry in data["media"]:
            if (entry.get("updatedAt") or 0) < since:
                reached_cutoff = True
                break
            upsert_entry(conn, entry)
            updated += 1
        conn.commit()
        if reached_cutoff or not data["pageInfo"]["hasNextPage"]:
            break
        if page >= MAX_OFFSET_PAGES:
            print(
                f"[sync] {media_type} incremental hit AniList's {MAX_OFFSET_PAGES * PER_PAGE}-row"
                " offset limit before reaching the cutoff; older updates were skipped."
                " Run a full sync to catch up."
            )
            break
        page += 1
    print(f"[sync] {media_type} incremental: {updated} entries updated since {since}")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync the AniList catalog")
    parser.add_argument("--type", required=True, choices=["anime", "manga"])
    parser.add_argument(
        "--since",
        type=int,
        default=None,
        help="unix timestamp; run an incremental sync instead of a full sync",
    )
    parser.add_argument("--min-interval", type=float, default=settings.anilist_min_interval)
    parser.add_argument("--cache-dir", default=settings.http_cache_dir)
    args = parser.parse_args()

    media_type = args.type.upper()
    client = RateLimitedClient(min_interval=args.min_interval, cache_dir=args.cache_dir)
    try:
        with psycopg.connect(settings.database_url) as conn:
            if args.since is not None:
                incremental_sync(client, conn, media_type, args.since)
            else:
                full_sync(client, conn, media_type)
    finally:
        client.close()


if __name__ == "__main__":
    main()
