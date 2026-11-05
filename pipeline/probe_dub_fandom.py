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

# Cross-reference templates seen on real pages. The first one is the
# reason this source is worth more than the first recon assumed: a page
# that carries {{AniList title|...}} can be joined by id, which removes
# the title matching that caps ANN at 31.8% and that got Discode nothing.
ID_TEMPLATES = (
    "AniList title",
    "ANN title",
    "MyAnimeList title",
    "DubDB title",
    "IMDb title",
)

HEADING_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)
# A whole flat template invocation, so the recon can show what its
# parameters look like instead of guessing at them.
TEMPLATE_CALL_RE = re.compile(r"\{\{\s*([^|}\n]+?)\s*(\|[^{}]*)?\}\}")
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


def template_calls(wikitext: str, names: tuple[str, ...] = ID_TEMPLATES) -> list[str]:
    """Raw invocations of the named templates, verbatim.

    Printed rather than parsed. The parameter shape of {{AniList title}}
    is exactly the kind of thing to read off a real page instead of
    assuming, which is what the English-section guess got wrong.
    """
    found = []
    for match in TEMPLATE_CALL_RE.finditer(wikitext):
        if match.group(1).strip() in names:
            found.append(match.group(0))
    return found


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
            "id_templates": template_calls(wikitext),
            "cast_sample": section(wikitext, "cast")[:600],
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
    print(f"  id templates: {report['id_templates'] or 'none'}")
    if report.get("cast_sample"):
        print("  --- cast section, first 600 chars ---")
        for line in report["cast_sample"].splitlines():
            print(f"  | {line}")


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


# {{AniList title|#=1}} - a named parameter called "#", read off a real
# page rather than assumed. Cowboy Bebop's value is 1, which is its actual
# AniList id, so the join is verifiable and not merely plausible.
ANILIST_ID_RE = re.compile(r"\{\{\s*AniList title\s*\|\s*#\s*=\s*(\d+)\s*\}\}")

# The cast tables seen on real pages have a "Dub Actor" column beside
# "Seiyu". Its presence is evidence the page names a dub cast at all; it
# does NOT say which language, and nothing here pretends otherwise.
DUB_ACTOR_RE = re.compile(r"Dub\s*Actor", re.IGNORECASE)

# MediaWiki accepts fifty titles per query for anonymous clients, which
# turns a 634-page census from 634 requests into thirteen.
BATCH_SIZE = 50


def anilist_id(wikitext: str) -> int | None:
    match = ANILIST_ID_RE.search(wikitext)
    return int(match.group(1)) if match else None


def names_a_dub_cast(wikitext: str) -> bool:
    """Whether the page has a dub-actor column, in any language."""
    return bool(DUB_ACTOR_RE.search(section(wikitext, "cast") or wikitext))


def pages_with_template(
    client: RateLimitedClient, template: str, cap: int = 20
) -> tuple[list[str], bool]:
    """Article-namespace pages embedding a template, and whether that is all."""
    titles: list[str] = []
    params = {
        "action": "query",
        "list": "embeddedin",
        "eititle": f"Template:{template}",
        "eilimit": "500",
        "einamespace": "0",
        "format": "json",
        "formatversion": "2",
    }
    for _ in range(cap):
        data = client.request("GET", API_URL, params=params)
        titles.extend(item["title"] for item in data.get("query", {}).get("embeddedin", []))
        cont = data.get("continue", {}).get("eicontinue")
        if not cont:
            return titles, True
        params["eicontinue"] = cont
    return titles, False


def fetch_many(client: RateLimitedClient, titles: list[str]) -> dict[str, str]:
    """Wikitext for up to BATCH_SIZE pages in one request."""
    data = client.request(
        "GET",
        API_URL,
        params={
            "action": "query",
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "titles": "|".join(titles),
            "format": "json",
            "formatversion": "2",
        },
    )
    out: dict[str, str] = {}
    for page in data.get("query", {}).get("pages", []):
        if page.get("missing"):
            continue
        revisions = page.get("revisions") or []
        if not revisions:
            continue
        out[page["title"]] = revisions[0].get("slots", {}).get("main", {}).get("content", "")
    return out


def run_census(use_db: bool) -> None:
    """Every AniList-linked page, against our own dub gap.

    A census rather than a sample, because 634 pages is small enough to
    read in full: batched fifty at a time, the whole wiki's AniList-linked
    corpus is about thirteen requests. A sample would add uncertainty to a
    number we can simply have.
    """
    client = RateLimitedClient(min_interval=MIN_INTERVAL, max_retries=2, timeout=30.0)
    titles, complete = pages_with_template(client, "AniList title")
    print(f"pages embedding AniList title: {len(titles)}{'' if complete else ' (capped)'}")

    by_id: dict[int, str] = {}
    with_cast: set[int] = set()
    no_id = 0
    for start in range(0, len(titles), BATCH_SIZE):
        batch = titles[start : start + BATCH_SIZE]
        try:
            pages = fetch_many(client, batch)
        except Exception as exc:  # noqa: BLE001
            print(f"  batch at {start} FAILED ({type(exc).__name__}: {exc})")
            continue
        for title, wikitext in pages.items():
            media_id = anilist_id(wikitext)
            if media_id is None:
                no_id += 1
                continue
            by_id[media_id] = title
            if names_a_dub_cast(wikitext):
                with_cast.add(media_id)
        print(f"  read {min(start + BATCH_SIZE, len(titles))}/{len(titles)}", flush=True)

    print(f"\nusable AniList ids: {len(by_id)}")
    print(f"  pages embedding the template but with no parsable id: {no_id}")
    print(f"  of the ids, pages with a dub-actor column: {len(with_cast)}")
    if not use_db:
        print("\nno database here; the overlap with our own gap is the NAS half.")
        return

    import psycopg
    from psycopg.rows import dict_row

    from api.config import settings

    # english_answer restates api/filters.py's English clause, because that
    # clause is what decides whether a title appears under the dub filter.
    # The any-language column is kept beside it to show the difference: a
    # title with a known Portuguese cast "has a dub answer" while still
    # being invisible to a reader filtering for English, which is exactly
    # the case that started this investigation.
    sql = """
        SELECT m.id,
               m.title_english,
               m.title_romaji,
               (m.dub_languages @> ARRAY['en']::text[]
                OR m.measured_dub_languages @> ARRAY['en']::text[]
                OR m.english_va_count > 0
                OR m.english_dub_inferred)                      AS english_answer,
               ((m.english_va_count IS NOT NULL AND m.english_va_count > 0)
                OR m.english_dub_inferred
                OR coalesce(array_length(m.dub_languages, 1), 0) > 0
                OR coalesce(array_length(m.measured_dub_languages, 1), 0) > 0)
                                                                AS any_answer
        FROM media m
        WHERE m.id = ANY(%(ids)s)
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        rows = conn.execute(sql, {"ids": list(by_id)}).fetchall()

    any_known = any_gap = any_gap_cast = 0
    english_known = english_gap = 0
    english_gap_titles: list[str] = []
    for row in rows:
        if row["any_answer"]:
            any_known += 1
        else:
            any_gap += 1
            if row["id"] in with_cast:
                any_gap_cast += 1
        if row["english_answer"]:
            english_known += 1
            continue
        english_gap += 1
        if row["id"] in with_cast:
            title = row["title_english"] or row["title_romaji"] or f"#{row['id']}"
            english_gap_titles.append(f"{row['id']} {title}")

    print(f"\nof {len(by_id)} wiki ids, {len(rows)} exist in our catalog")
    print("\nany dub language known:")
    print(f"  already answered: {any_known}")
    print(f"  no answer at all: {any_gap}  (of those, wiki names a cast: {any_gap_cast})")
    print("\nENGLISH specifically - the clause api/filters.py actually applies:")
    print(f"  English dub known: {english_known}")
    print(f"  English unknown: {english_gap}")
    print(f"  English unknown AND the wiki names a dub cast: {len(english_gap_titles)}")
    for line in english_gap_titles[:40]:
        print(f"    {line}")
    if len(english_gap_titles) > 40:
        print(f"    ... and {len(english_gap_titles) - 40} more")
    print(
        "\nThe English figure is the one to decide on: a title with a known"
        "\nPortuguese cast counts as answered in the first block while still"
        "\nbeing invisible to a reader filtering for an English dub. What the"
        '\nwiki names is still only "Dub Actor" - a dub, not an English one -'
        "\nso treat even this as an upper bound until a parser reads the"
        "\nlanguage out of the page."
    )


def count_pages_with_template(client: RateLimitedClient, template: str, cap: int = 20) -> dict:
    """How many wiki pages embed a template, via list=embeddedin.

    This is the number that decides whether the wiki can be joined by id
    at all. If {{AniList title}} appears on a few hundred pages it is a
    curiosity; if it appears on thousands, it is a mapping table that
    needs no title matching.
    """
    titles, complete = pages_with_template(client, template, cap)
    return {
        "template": template,
        "count": len(titles),
        "complete": complete,
        "sample": titles[:5],
    }


def run_coverage() -> None:
    """How many pages carry each cross-reference id template."""
    client = RateLimitedClient(min_interval=MIN_INTERVAL, max_retries=2, timeout=20.0)
    for template in ID_TEMPLATES:
        try:
            result = count_pages_with_template(client, template)
        except Exception as exc:  # noqa: BLE001
            print(f"{template}: REQUEST FAILED ({type(exc).__name__}: {exc})")
            continue
        suffix = "" if result["complete"] else " (capped, more exist)"
        print(f"{template}: {result['count']} article pages{suffix}")
        print(f"  e.g. {', '.join(result['sample'])}")


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

    sub.add_parser(
        "coverage",
        help="how many wiki pages carry each cross-reference id template",
    )

    census = sub.add_parser(
        "census",
        help="every AniList-linked page, counted against our own dub gap",
    )
    census.add_argument(
        "--no-db",
        action="store_true",
        help="fetch and parse only; skip the catalog comparison (for CI)",
    )

    probe = sub.add_parser("probe", help="measure against the catalog's dub gap")
    probe.add_argument("--sample", type=int, default=100)
    probe.add_argument("--seed", type=int, default=20261105)

    args = parser.parse_args()
    if args.command == "recon":
        run_recon(args.title or DEFAULT_TITLES)
    elif args.command == "coverage":
        run_coverage()
    elif args.command == "census":
        run_census(use_db=not args.no_db)
    else:
        run_probe(args.sample, args.seed)


if __name__ == "__main__":
    main()
