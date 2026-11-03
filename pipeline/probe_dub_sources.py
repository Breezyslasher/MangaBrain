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
from collections import Counter
from xml.etree import ElementTree

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
    ("Cowboy Bebop: The Movie (id 353)", f"{ANN_CDN}?anime=353"),
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


def describe_xml(body: str) -> None:
    """Print the document's real shape, compactly.

    A raw dump of a 60kB response buries the evidence in CI's log, and
    summarising from memory is how this project already lost two runs. So walk
    the parsed tree and report observed tags and attributes, plus every element
    carrying a language attribute verbatim.
    """
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        log(f"    not parseable as XML: {exc}")
        log(f"    first 300 chars: {body[:300]!r}")
        return

    log(f"    root <{root.tag}> attrs={dict(root.attrib)}")
    for child in list(root)[:3]:
        kids = Counter(sub.tag for sub in child)
        log(f"    <{child.tag}> attrs={dict(child.attrib)}")
        log(f"      children: {dict(kids)}")

    # The whole question: is CAST recorded per language? An earlier pass
    # printed the first lang-bearing elements in document order and they were
    # all <info> (titles, websites), which says nothing about the cast.
    lang_bearing = [el for el in root.iter() if any("lang" in k.lower() for k in el.attrib)]
    by_tag = Counter(el.tag for el in lang_bearing)
    log(f"    lang-bearing elements by tag: {dict(by_tag)}")

    casts = list(root.iter("cast"))
    log(f"    <cast> elements: {len(casts)}")
    tagged = [c for c in casts if any("lang" in k.lower() for k in c.attrib)]
    log(f"    <cast> carrying a lang attribute: {len(tagged)}/{len(casts)}")
    log(f"    lang values on <cast>: {dict(Counter(c.attrib.get('lang') for c in casts))}")
    for cast in casts[:4]:
        inner = {sub.tag: (sub.text or "").strip()[:36] for sub in list(cast)[:3]}
        log(f"      <cast {dict(cast.attrib)}> {inner}")

    # <release> may name the dubbed release instead; worth seeing either way.
    for rel in list(root.iter("release"))[:3]:
        log(f"      <release {dict(rel.attrib)}> text={(rel.text or '').strip()[:60]!r}")

    gross = Counter(el.tag for el in root.iter())
    log(f"    every tag seen: {dict(gross)}")


def run_dump() -> None:
    log("=== ANN response shape (observed, not recalled) ===")
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
        for label, url in ANN_DUMPS:
            status, ctype, body = get(client, url)
            time.sleep(MIN_INTERVAL)
            log(f"--- {label}: HTTP {status} {ctype.split(';')[0]} ({len(body)} bytes) ---")
            if status != 200:
                log(f"    body: {body[:200]!r}")
                continue
            describe_xml(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe other dub data sources")
    parser.add_argument("--reachability", action="store_true")
    parser.add_argument("--dump", action="store_true")
    args = parser.parse_args()
    if not (args.reachability or args.dump):
        parser.error("pass --reachability and/or --dump")
    # Dump first, reachability last: CI serves only the tail of a job log,
    # so the summary table has to be the final thing printed.
    if args.dump:
        run_dump()
    if args.reachability:
        run_reachability()


if __name__ == "__main__":
    main()
