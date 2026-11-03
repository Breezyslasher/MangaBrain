"""Find out which other databases can answer "does this anime have an English dub".

AniList's own answer degrades with obscurity: measured coverage runs 100% at
the top of the catalog, 70% around rank 1000, 50% around rank 3000 and 20%
around rank 5000 (pipeline.probe_dub_coverage). Whether that is the real dub
rate or a data gap cannot be settled from AniList alone, so this probes other
sources.

Two stages, because guessing a response schema is how the AniList probe wasted
two runs reporting confident zeros:

  --reachability  which candidate hosts answer at all from here, and with what
  --dump          raw responses from the reachable ones for a known-dubbed
                  title, so the parser is written against observed data

Nothing is parsed into a verdict yet; this run exists to produce evidence to
write the parser from. Hosts needing a key or client registration are probed
for reachability only, and reported as needing a secret rather than guessed at.

Usage:
    python -m pipeline.probe_dub_sources --reachability
    python -m pipeline.probe_dub_sources --dump
"""

import argparse
import time

import httpx

# (label, url, needs_secret). Reachability only: a 401/403 still proves the
# host is routable, which is the thing being measured.
ANN_CDN = "https://cdn.animenewsnetwork.com/encyclopedia/api.xml"
WIKIDATA_QUERY = "SELECT ?s WHERE { ?s wdt:P31 wd:Q63952888 } LIMIT 1"

CANDIDATES: tuple[tuple[str, str, bool], ...] = (
    # Encyclopedia with cast broken down by language; the strongest candidate,
    # since an English cast listing is direct evidence of a dub.
    ("ANN encyclopedia (cdn)", f"{ANN_CDN}?anime=4658", False),
    ("ANN encyclopedia (www)", "https://www.animenewsnetwork.com/encyclopedia/api.php", False),
    # Records audio languages per release, but registration and strict terms.
    ("AniDB", "https://api.anidb.net:9001/httpapi?request=anime&aid=1", True),
    # Has anime entries and spoken_languages, but needs an API key.
    ("TMDB", "https://api.themoviedb.org/3/tv/1429", True),
    # Tracks sub and dub release schedules.
    ("AnimeSchedule", "https://animeschedule.net/api/v3/anime?q=cowboy+bebop", True),
    # Public SPARQL; dubbing statements exist but coverage is the question.
    ("Wikidata SPARQL", "https://query.wikidata.org/sparql", False),
    ("Simkl", "https://api.simkl.com/anime/1", True),
    # Retested: it refused GitHub's runners and the deployment host alike.
    ("Jikan (MAL)", "https://api.jikan.moe/v4/anime/1", False),
)

# ANN shapes worth seeing. Cowboy Bebop is ANN anime id 4658 and is certainly
# dubbed, so a response that cannot show an English cast for it tells us the
# source is unusable regardless of coverage elsewhere.
ANN_DUMPS: tuple[tuple[str, str], ...] = (
    ("anime by id", f"{ANN_CDN}?anime=4658"),
    ("anime by title", f"{ANN_CDN}?title=~cowboy+bebop"),
    ("api docs page", "https://www.animenewsnetwork.com/encyclopedia/api.php"),
)

# ANN asks for no more than one request per second and for caching; this stays
# well under that and makes only a handful of requests in total.
MIN_INTERVAL = 2.5
UA = "MangaBrain/0.1 (self-hosted recommendation engine; coverage probe)"


def log(message: str) -> None:
    print(f"[sources] {message}", flush=True)


def get(client: httpx.Client, url: str) -> tuple[int | None, str, str]:
    """Return (status, content_type, body_or_error). Never raises."""
    params = None
    if url.endswith("/sparql"):
        params = {"format": "json", "query": WIKIDATA_QUERY}
    try:
        resp = client.get(url, params=params)
    except Exception as exc:  # noqa: BLE001 - reachability is the measurement
        return None, "", f"{type(exc).__name__}: {exc}"
    return resp.status_code, resp.headers.get("content-type", ""), resp.text


def run_reachability() -> None:
    log("=== which hosts answer from here ===")
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
        for label, url, needs_secret in CANDIDATES:
            status, ctype, body = get(client, url)
            time.sleep(MIN_INTERVAL)
            note = " [needs a key/registration]" if needs_secret else ""
            if status is None:
                log(f"  {label:<24} UNREACHABLE  {body[:90]}{note}")
                continue
            verdict = "routable"
            if status in (401, 403):
                verdict = "routable, rejected without credentials"
            log(f"  {label:<24} HTTP {status} {ctype.split(';')[0]:<24} {verdict}{note}")


def run_dump() -> None:
    log("=== raw ANN responses (write the parser from these, not from memory) ===")
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
        for label, url in ANN_DUMPS:
            status, ctype, body = get(client, url)
            time.sleep(MIN_INTERVAL)
            log(f"--- {label}: HTTP {status} {ctype} ({len(body)} bytes) ---")
            print(body[:9000], flush=True)
            # The question is whether cast is tagged by language at all.
            lowered = body.lower()
            for needle in ('lang="en"', "lang='en'", "<cast", "english"):
                log(f"    contains {needle!r}: {needle in lowered}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe other dub data sources")
    parser.add_argument("--reachability", action="store_true")
    parser.add_argument("--dump", action="store_true")
    args = parser.parse_args()
    if not (args.reachability or args.dump):
        parser.error("pass --reachability and/or --dump")
    if args.reachability:
        run_reachability()
    if args.dump:
        run_dump()


if __name__ == "__main__":
    main()
