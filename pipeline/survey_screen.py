"""Could this engine rank TV shows and films the way it ranks anime?

The recommender needs a synopsis worth embedding, tags worth comparing,
and a stable id, for most of a catalogue - not for its famous corner. An
earlier reconnaissance (pipeline.probe_media_sources screen) only got as
far as "TMDB needs a key" and "Wikidata has one-line descriptions, not
plots". This measures the two real candidates on unbiased samples:

  TMDB    films and TV. Its daily id exports (files.tmdb.org, no key)
          list every id, so the sample is drawn uniformly from the whole
          catalogue rather than from a popular-first search. Each sampled
          title is fetched with its keywords, which are the tag vocabulary
          the anime side gets from AniList. Needs a free API key.
  TVmaze  TV only, no key. Shows are paged by id, so random pages are a
          uniform sample too. Genres only, no keywords.

Per source and type it reports: the share with a synopsis of at least
--min-synopsis characters, median synopsis length, median genres and
keywords, the share that is Japanese animation (already covered by the
anime catalogue, so a duplicate to drop rather than a gain), and the
usable catalogue size that implies.

Popularity is in TMDB's export and is ignored: the sample is uniform over
ids, and no popularity, vote or rating field is read from the details.

    TMDB_API_KEY=... python -m pipeline.survey_screen
    python -m pipeline.survey_screen --sample 300 --json screen.json

TMDB_API_KEY may be a v3 API key or a v4 read access token.
"""

import argparse
import gzip
import json
import os
import random
import re
import statistics
import time
from datetime import UTC, datetime, timedelta

import httpx

from pipeline.client import RateLimitedClient

TMDB_API = "https://api.themoviedb.org/3"
TMDB_EXPORTS = "https://files.tmdb.org/p/exports"
TVMAZE = "https://api.tvmaze.com"

MIN_SYNOPSIS = 200
# TVmaze allows 20 calls per 10 seconds; TMDB about 50 a second.
TVMAZE_INTERVAL = 0.6
TMDB_INTERVAL = 0.05
TAG = re.compile(r"<[^>]+>")


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def summarise(rows: list[dict], total: int, min_synopsis: int) -> dict:
    """rows: {synopsis, genres, keywords, anime} per sampled title."""
    n = len(rows)
    usable = [r for r in rows if r["synopsis"] >= min_synopsis]
    anime = sum(1 for r in rows if r["anime"])
    usable_not_anime = sum(1 for r in usable if not r["anime"])
    return {
        "sampled": n,
        "catalogue": total,
        "usable_pct": _pct(len(usable), n),
        "synopsis_median": _median([r["synopsis"] for r in rows]),
        "synopsis_median_usable": _median([r["synopsis"] for r in usable]),
        "genres_median": _median([r["genres"] for r in rows]),
        "keywords_median": _median([r["keywords"] for r in rows]),
        "keywords_median_usable": _median([r["keywords"] for r in usable]),
        "keywords_3plus_usable_pct": _pct(
            sum(1 for r in usable if r["keywords"] >= 3), len(usable)
        ),
        "japanese_animation_pct": _pct(anime, n),
        "usable_estimate": round(total * usable_not_anime / n) if n else 0,
    }


# ------------------------------------------------------------------ TMDB


def tmdb_export_ids(kind: str) -> list[int]:
    """Every id in the newest daily export, adult and video entries dropped.

    kind is "movie" or "tv_series". The export is published once a day; the
    newest may not be up yet, so the last few days are tried in turn.
    """
    for back in range(4):
        day = datetime.now(UTC) - timedelta(days=back)
        url = f"{TMDB_EXPORTS}/{kind}_ids_{day:%m_%d_%Y}.json.gz"
        resp = httpx.get(url, timeout=120, follow_redirects=True)
        if resp.status_code != 200:
            continue
        ids = []
        for line in gzip.decompress(resp.content).splitlines():
            entry = json.loads(line)
            if entry.get("adult") or entry.get("video"):
                continue
            ids.append(int(entry["id"]))
        print(f"  {kind}: {len(ids):,} ids in the {day:%Y-%m-%d} export", flush=True)
        return ids
    raise RuntimeError(f"no TMDB {kind} export found in the last four days")


def tmdb_client(key: str) -> tuple[RateLimitedClient, dict]:
    # A v4 read access token is a JWT (three dot-separated parts); a v3
    # key is 32 hex characters and goes in the query string.
    if key.count(".") == 2:
        return RateLimitedClient(
            min_interval=TMDB_INTERVAL,
            timeout=30.0,
            max_retries=2,
            extra_headers={"Authorization": f"Bearer {key}"},
        ), {}
    return RateLimitedClient(min_interval=TMDB_INTERVAL, timeout=30.0, max_retries=2), {
        "api_key": key
    }


def tmdb_row(client: RateLimitedClient, auth: dict, kind: str, tmdb_id: int) -> dict | None:
    path = "movie" if kind == "movie" else "tv"
    try:
        data = client.request(
            "GET",
            f"{TMDB_API}/{path}/{tmdb_id}",
            params={**auth, "append_to_response": "keywords", "language": "en-US"},
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            raise
        return None  # a deleted id, between the export and now
    except (httpx.TransportError, RuntimeError):
        return None
    keywords = data.get("keywords") or {}
    keywords = keywords.get("keywords") or keywords.get("results") or []
    genres = [g.get("name") for g in data.get("genres") or []]
    origin = data.get("origin_country") or [
        c.get("iso_3166_1") for c in data.get("production_countries") or []
    ]
    return {
        "synopsis": len((data.get("overview") or "").strip()),
        "genres": len(genres),
        "keywords": len(keywords),
        "anime": "Animation" in genres
        and ("JP" in origin or data.get("original_language") == "ja"),
    }


def survey_tmdb(key: str, size: int, min_synopsis: int, rng: random.Random) -> dict:
    client, auth = tmdb_client(key)
    out = {}
    try:
        for kind in ("movie", "tv_series"):
            ids = tmdb_export_ids(kind)
            sample = rng.sample(ids, min(size, len(ids)))
            rows = []
            started = time.monotonic()
            for i, tmdb_id in enumerate(sample, 1):
                row = tmdb_row(client, auth, kind, tmdb_id)
                if row is not None:
                    rows.append(row)
                if i % 100 == 0:
                    print(f"    {kind}: {i}/{len(sample)}", flush=True)
            print(f"    {kind}: {len(rows)} fetched in {time.monotonic() - started:.0f}s")
            out[kind] = summarise(rows, len(ids), min_synopsis)
    finally:
        client.close()
    return out


# ---------------------------------------------------------------- TVmaze


def survey_tvmaze(size: int, min_synopsis: int, rng: random.Random) -> dict:
    """Random pages of /shows (250 shows each, by id) until `size` shows."""
    client = RateLimitedClient(min_interval=TVMAZE_INTERVAL, timeout=30.0, max_retries=2)
    try:
        # Find the last page by doubling then bisecting: a page past the
        # end answers 404.
        def exists(page: int) -> bool:
            try:
                return bool(client.request("GET", f"{TVMAZE}/shows", params={"page": page}))
            except httpx.HTTPStatusError:
                return False

        high = 1
        while exists(high):
            high *= 2
        low = high // 2
        while low + 1 < high:
            mid = (low + high) // 2
            if exists(mid):
                low = mid
            else:
                high = mid
        last_page = low
        pages = list(range(last_page + 1))
        rng.shuffle(pages)
        shows: list[dict] = []
        total_estimate = (last_page + 1) * 250
        for page in pages:
            shows.extend(client.request("GET", f"{TVMAZE}/shows", params={"page": page}))
            if len(shows) >= size * 3:
                break
        shows = rng.sample(shows, min(size, len(shows)))
    finally:
        client.close()
    rows = []
    for show in shows:
        summary = TAG.sub("", show.get("summary") or "").strip()
        network = (show.get("network") or show.get("webChannel") or {}).get("country") or {}
        rows.append(
            {
                "synopsis": len(summary),
                "genres": len(show.get("genres") or []),
                "keywords": 0,
                "anime": show.get("type") == "Animation"
                and (network.get("code") == "JP" or show.get("language") == "Japanese"),
            }
        )
    print(f"  TVmaze: about {total_estimate:,} shows; sampled {len(rows)}", flush=True)
    return {"tv": summarise(rows, total_estimate, min_synopsis)}


# ---------------------------------------------------------------- report


def report(results: dict, min_synopsis: int) -> None:
    print()
    print(f"Usable = a synopsis of at least {min_synopsis} characters.")
    for source, kinds in results.items():
        for kind, r in kinds.items():
            print()
            print(f"{source} {kind} (sample {r['sampled']} of {r['catalogue']:,}):")
            print(f"  usable                      {r['usable_pct']}%")
            print(
                f"  synopsis median             {r['synopsis_median']:.0f} chars"
                f" ({r['synopsis_median_usable']:.0f} where usable)"
            )
            print(f"  genres median               {r['genres_median']}")
            if source == "TMDB":
                print(
                    f"  keywords median             {r['keywords_median']}"
                    f" ({r['keywords_median_usable']} where usable;"
                    f" {r['keywords_3plus_usable_pct']}% of usable have 3+)"
                )
            print(f"  Japanese animation          {r['japanese_animation_pct']}%")
            print(f"  usable, not anime, about    {r['usable_estimate']:,} titles")


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure TV and film sources")
    parser.add_argument("--sample", type=int, default=300, help="titles per source and type")
    parser.add_argument("--min-synopsis", type=int, default=MIN_SYNOPSIS)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--json", help="also write the result to this file")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    results: dict = {}
    print("TVmaze", flush=True)
    try:
        results["TVmaze"] = survey_tvmaze(args.sample, args.min_synopsis, rng)
    except Exception as exc:  # noqa: BLE001 - one source must not stop the other
        print(f"  TVmaze failed: {type(exc).__name__}: {exc}", flush=True)
    key = os.environ.get("TMDB_API_KEY", "").strip()
    print("TMDB", flush=True)
    if not key:
        print("  skipped: set TMDB_API_KEY (themoviedb.org > Settings > API, free)")
    else:
        try:
            results["TMDB"] = survey_tmdb(key, args.sample, args.min_synopsis, rng)
        except Exception as exc:  # noqa: BLE001
            print(f"  TMDB failed: {type(exc).__name__}: {exc}", flush=True)
    report(results, args.min_synopsis)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(results, fh, indent=2)


if __name__ == "__main__":
    main()
