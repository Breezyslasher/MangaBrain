"""Does the Dubbing Wiki know about dubs our sources miss?

The fourth candidate source, measured the same way as the others rather
than adopted on the strength of looking promising. What makes it worth
asking about: it is a MediaWiki, so api.php is a real public API (no
scraping, which the project forbids), and a wiki's contributors enter
exactly the thing AniList's editors stop entering as titles get obscure.

What makes it structurally weaker than MAL, and the reason this probe
reports matching separately from coverage: the wiki carries no AniList or
MAL ids. Every lookup is by title, which is the mechanism that caps ANN at
31.8% of the catalog and that returned "unmatched" for The Amazing 3 until
pipeline.title_variants learned to reach "Amazing Three, The". A wrong
match is worse than no answer - it marks a sub-only title as dubbed, and
the reader finds out an episode in - so a page is only accepted when its
normalised title matches the catalog entry's own.

Two subcommands, because the questions need different things:

    recon   fetches a handful of named titles and reports what their pages
            are SHAPED like - sections, templates, whether an English cast
            section exists at all. It needs network and no database, so it
            runs anywhere, including a CI runner. Nothing here parses a
            cast into a result: the parser below is a first guess at a
            format nobody has looked at yet, and recon is how we find out
            what the format actually is before trusting it.

    probe   samples the catalog's dub-unknown titles, stratified by
            obscurity, and reports what fraction the wiki can answer for.
            Needs the database, so it runs on the deployment host.

    python -m pipeline.probe_dub_fandom recon --title "The Amazing 3"
    docker exec mangabrain-worker-1 python -m pipeline.probe_dub_fandom probe
"""

import argparse
import re
from collections import Counter

from pipeline.ann import normalize
from pipeline.client import RateLimitedClient

API_URL = "https://dubbing.fandom.com/api.php"

# A wiki is a volunteer project; one request a second is plenty for a probe.
MIN_INTERVAL = 1.0

SEARCH_LIMIT = 10

HEADING_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)
TEMPLATE_RE = re.compile(r"\{\{\s*([^|}\n]+)")
LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def search(client: RateLimitedClient, title: str) -> list[str]:
    """Page titles the wiki's search returns for a title."""
    data = client.request(
        "GET",
        API_URL,
        params={
            "action": "query",
            "list": "search",
            "srsearch": title,
            "srlimit": SEARCH_LIMIT,
            "format": "json",
        },
    )
    return [hit["title"] for hit in data.get("query", {}).get("search", [])]


def fetch_wikitext(client: RateLimitedClient, page: str) -> str:
    """The raw wikitext of a page, or "" if the page is missing."""
    data = client.request(
        "GET",
        API_URL,
        params={
            "action": "query",
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "titles": page,
            "format": "json",
            "formatversion": "2",
        },
    )
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return ""
    revisions = pages[0].get("revisions") or []
    if not revisions:
        return ""
    return revisions[0].get("slots", {}).get("main", {}).get("content", "")


def confirm(catalog_title: str, page_title: str) -> bool:
    """Whether a search hit may be taken as this title's page.

    Search returns anything that shares words, so "Discode" brings back
    every page mentioning it. Accepting a hit because it came back first
    is how a title-matched source asserts another show's dub.
    """
    return normalize(catalog_title) == normalize(page_title)


def headings(wikitext: str) -> list[str]:
    return [match.group(2) for match in HEADING_RE.finditer(wikitext)]


def templates(wikitext: str) -> Counter:
    return Counter(name.strip() for name in TEMPLATE_RE.findall(wikitext))


def section(wikitext: str, name: str) -> str:
    """The body under the first heading containing `name`, case-insensitive.

    Ends at the next heading of the same or higher level, so a subsection
    stays with its parent and the next language does not.
    """
    wanted = name.lower()
    for match in HEADING_RE.finditer(wikitext):
        if wanted not in match.group(2).lower():
            continue
        level = len(match.group(1))
        start = match.end()
        for following in HEADING_RE.finditer(wikitext, start):
            if len(following.group(1)) <= level:
                return wikitext[start : following.start()]
        return wikitext[start:]
    return ""


def english_cast(wikitext: str) -> list[str]:
    """Provisional: names linked under an English heading.

    A first guess at a format nobody has inspected yet. `recon` exists to
    replace this guess with the real structure; until it has, a number
    produced from this is not evidence of anything.
    """
    body = section(wikitext, "english")
    if not body:
        return []
    names = [name.strip() for name in LINK_RE.findall(body)]
    # Wiki links also point at files, categories and the show itself.
    return [
        name
        for name in names
        if name and ":" not in name and not name.lower().startswith("category")
    ]


def recon_one(client: RateLimitedClient, title: str) -> dict:
    """What a title's page is shaped like, without interpreting it."""
    hits = search(client, title)
    confirmed = next((hit for hit in hits if confirm(title, hit)), None)
    report: dict = {
        "title": title,
        "hits": hits,
        "confirmed": confirmed,
    }
    if confirmed is None:
        return report
    wikitext = fetch_wikitext(client, confirmed)
    report.update(
        {
            "chars": len(wikitext),
            "headings": headings(wikitext),
            "templates": templates(wikitext).most_common(10),
            "has_english_section": bool(section(wikitext, "english")),
            "english_links": len(english_cast(wikitext)),
            "table_markup": wikitext.count("{|"),
        }
    )
    return report


def print_recon(report: dict) -> None:
    print(f"\n--- {report['title']}")
    if not report["hits"]:
        print("  search returned nothing")
        return
    print(f"  search hits: {', '.join(report['hits'][:5])}")
    if report["confirmed"] is None:
        # Not "the wiki has no dub for it": the wiki may hold the page
        # under a name this comparison does not reach, which is the whole
        # reason pipeline.title_variants exists for ANN.
        print("  no hit whose normalised title matches; UNMATCHED, not unknown")
        return
    print(f"  page: {report['confirmed']}  ({report['chars']} chars)")
    print(f"  headings: {report['headings'][:12]}")
    print(f"  templates: {report['templates']}")
    print(f"  wikitables: {report['table_markup']}")
    print(
        f"  english section: {report['has_english_section']}"
        f"  links under it: {report['english_links']}"
    )


# Controls worth including in any run: one title our sources handle, and
# the two this investigation failed on.
DEFAULT_TITLES = ["Cowboy Bebop", "The Amazing 3", "Discode"]


def run_recon(titles: list[str]) -> int:
    client = RateLimitedClient(min_interval=MIN_INTERVAL, max_retries=2, timeout=20.0)
    matched = 0
    for title in titles:
        try:
            report = recon_one(client, title)
        except Exception as exc:  # noqa: BLE001
            # A probe that reports its own failure as a finding has happened
            # four times in this investigation; this one says which it is.
            print(f"\n--- {title}\n  REQUEST FAILED: {type(exc).__name__}: {exc}")
            continue
        print_recon(report)
        matched += report["confirmed"] is not None
    print(f"\nmatched {matched}/{len(titles)} by normalised title")
    return matched


def run_probe(sample_size: int, seed: int) -> None:
    """Measure against the gap, on the deployment host."""
    import random

    import psycopg
    from psycopg.rows import dict_row

    from api.config import settings

    sql = """
        SELECT m.id, m.title_romaji, m.title_english, m.popularity
        FROM media m
        WHERE m.media_type = 'ANIME'
          AND m.english_va_count IS NULL
          AND NOT m.english_dub_inferred
          AND coalesce(array_length(m.dub_languages, 1), 0) = 0
          AND coalesce(array_length(m.measured_dub_languages, 1), 0) = 0
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        rows = conn.execute(sql).fetchall()
    if not rows:
        print("no titles without a dub answer; nothing to probe")
        return
    # Fixed seed, and a random sample rather than the first N: the head of
    # any ordering is the well-covered part, and sampling it is how an
    # earlier probe in this investigation produced a flattering number.
    random.Random(seed).shuffle(rows)
    rows = rows[:sample_size]

    client = RateLimitedClient(min_interval=MIN_INTERVAL, max_retries=2, timeout=20.0)
    matched = answered = failed = 0
    for row in rows:
        title = row["title_english"] or row["title_romaji"] or ""
        if not title:
            continue
        try:
            report = recon_one(client, title)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"{title}: REQUEST FAILED ({type(exc).__name__}: {exc})")
            continue
        if report["confirmed"] is None:
            continue
        matched += 1
        if report["english_links"]:
            answered += 1
        print(
            f"{title}: page={report['confirmed']}"
            f" english_section={report['has_english_section']}"
            f" links={report['english_links']}"
        )

    print(f"\nsampled {len(rows)} titles with no dub answer")
    print(f"  matched to a wiki page: {matched}")
    print(f"  of those, an English cast section with names: {answered}")
    print(f"  requests failed: {failed}")
    print(
        "\nThese counts use a provisional parser (see english_cast). Treat the"
        "\nmatch rate as the finding and the cast rate as an upper bound until"
        "\nrecon has confirmed the page format."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the Dubbing Wiki as a dub source")
    sub = parser.add_subparsers(dest="command", required=True)

    recon = sub.add_parser("recon", help="inspect page structure; no database needed")
    recon.add_argument("--title", action="append", default=None)

    probe = sub.add_parser("probe", help="measure against the catalog's dub gap")
    probe.add_argument("--sample", type=int, default=100)
    probe.add_argument("--seed", type=int, default=20261105)

    args = parser.parse_args()
    if args.command == "recon":
        run_recon(args.title or DEFAULT_TITLES)
    else:
        run_probe(args.sample, args.seed)


if __name__ == "__main__":
    main()
