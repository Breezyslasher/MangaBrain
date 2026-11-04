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
import re
import time
from collections import Counter
from html import unescape
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
    # Re-measured 2026-11-04 from a GitHub runner: ConnectError [Errno 101]
    # Network is unreachable - a ROUTE-level block, not a 403 or a 429, which
    # is what blocking cloud provider address ranges looks like. Every other
    # host here was routable from the same run, so it is Jikan's filter and
    # not the runner's egress.
    #
    # The old note here also claimed the deployment host, and that half was
    # never measured. It should not be inherited: a NAS on a residential
    # address is not in the range being filtered, and an empty mal_list_state
    # cannot tell a failed sync from a sync nobody ran. Test it there with
    # `probe_dub_mal --check`, or by POSTing /mal/<user>/refresh, which now
    # answers in about 32s instead of four minutes.
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

    # Does ANN carry a cross-reference to another database? If it does, this
    # can be joined on an id we already store instead of matched by title,
    # which is the difference between a reliable join and a guess. Most <info>
    # elements have no lang attribute and went unexamined until now.
    infos = list(root.iter("info"))
    log(f"    <info> types present: {dict(Counter(i.attrib.get('type') for i in infos))}")
    external = []
    for info in infos:
        haystack = " ".join([info.attrib.get("href", ""), (info.text or "")]).lower()
        if any(k in haystack for k in ("myanimelist", "anilist", "anidb", "kitsu", "mal.")):
            external.append(info)
    log(f"    <info> mentioning another database: {len(external)}")
    for info in external[:8]:
        log(f"      <info {dict(info.attrib)}> text={(info.text or '').strip()[:70]!r}")

    gross = Counter(el.tag for el in root.iter())
    log(f"    every tag seen: {dict(gross)}")


DOCS_URL = "https://www.animenewsnetwork.com/encyclopedia/api.php"
ANN_REPORTS = "https://cdn.animenewsnetwork.com/encyclopedia/reports.xml"


def run_reports() -> None:
    """See whether ANN can hand over its anime list in bulk.

    ANN exposes no MyAnimeList or AniList id (confirmed: zero <info> elements
    reference another database, and the documented lookups are by ANN id or by
    title only), so joining our catalog to ANN means matching on title. Doing
    that with one search request per title is thousands of requests against a
    source that asks for one per second.

    A bulk report would turn it into a single local join instead, and would
    also show how often titles collide across the whole ANN corpus rather than
    one query at a time. nlist is kept small here: the point is the shape, and
    asking for everything to learn the shape would be rude.
    """
    log("=== ANN bulk report shape ===")
    with httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
        for label, params in (
            ("anime report, 50 rows", {"id": "155", "type": "anime", "nlist": "50"}),
            (
                "anime report, skip 1000",
                {"id": "155", "type": "anime", "nskip": "1000", "nlist": "5"},
            ),
        ):
            try:
                resp = client.get(ANN_REPORTS, params=params)
                body, status = resp.text, resp.status_code
            except Exception as exc:  # noqa: BLE001 - a probe reports, it does not fail
                log(f"  {label}: {type(exc).__name__}: {exc}")
                continue
            time.sleep(MIN_INTERVAL)
            log(f"--- {label}: HTTP {status} ({len(body)} bytes) ---")
            if status != 200:
                log(f"    body: {body[:200]!r}")
                continue
            try:
                root = ElementTree.fromstring(body)
            except ElementTree.ParseError as exc:
                log(f"    not XML: {exc}; first 300 chars: {body[:300]!r}")
                continue
            log(f"    root <{root.tag}> attrs={dict(root.attrib)}")
            rows = list(root)
            log(f"    rows: {len(rows)}; tags: {dict(Counter(r.tag for r in rows))}")
            for row in rows[:4]:
                inner = {sub.tag: (sub.text or "").strip()[:40] for sub in list(row)[:6]}
                log(f"      <{row.tag} {dict(row.attrib)}> {inner}")


PARAM_HINTS = ("api.xml", "reports.xml", "?anime=", "?manga=", "title=", "id=", "nlist")


def run_docs() -> None:
    """Print only the parameter-ish lines of ANN's API docs.

    The whole page is a 60kB web page that buried everything else in CI's log
    last time, but it is also the only statement of which lookups the API
    supports, which is not something to assume.
    """
    log("=== ANN API documented parameters (markup stripped) ===")
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
        status, _, body = get(client, DOCS_URL)
    if status != 200:
        log(f"    HTTP {status}")
        return
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    seen: set[str] = set()
    for raw in text.splitlines():
        line = unescape(raw).strip()
        if not (8 < len(line) < 240):
            continue
        if not any(hint in line for hint in PARAM_HINTS):
            continue
        if line in seen:
            continue
        seen.add(line)
        log(f"    {line}")


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
    parser.add_argument("--docs", action="store_true", help="ANN's documented parameters")
    parser.add_argument("--reports", action="store_true", help="ANN bulk report shape")
    args = parser.parse_args()
    if not (args.reachability or args.dump or args.docs or args.reports):
        parser.error("pass --reachability, --dump, --docs and/or --reports")
    # Dump first, reachability last: CI serves only the tail of a job log,
    # so the summary table has to be the final thing printed.
    if args.dump:
        run_dump()
    if args.docs:
        run_docs()
    if args.reports:
        run_reports()
    if args.reachability:
        run_reachability()


if __name__ == "__main__":
    main()
