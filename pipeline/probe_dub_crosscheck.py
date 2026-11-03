"""Measure how often AniList's silence about a dub is wrong, using ANN.

This is the measurement the whole dub investigation has been missing. AniList's
English-cast coverage falls off with obscurity (100% at the top of the catalog,
70% at rank ~1000, 50% at ~3000, 20% at ~5000), and from AniList alone there is
no way to tell whether that is the real dub rate or a data gap. The plan was to
bound it against MAL via Jikan, but api.jikan.moe refuses GitHub's runners
outright (Errno 101) and has been failing from the deployment host too.

ANN replaces it, and is arguably the better instrument: its encyclopedia tags
every cast entry by language (58/58 on the sampled title, across nine
languages) and is editorially curated rather than crowd-entered, so the side
entries AniList tends to miss - sequels, seasons, OVAs whose parent franchise
is dubbed - are the kind of thing it plausibly has.

Method: sample AniList by popularity stratum, keep the titles where AniList
records a cast but credits no English voice actor, look each up on ANN, and
report how many ANN says have an English cast. That fraction is the
false-negative rate of the shipped `dubbed` filter.

Matching is the weak point and is treated as such. A wrong match asserts a dub
belonging to a different show, so a title resolves only when exactly one ANN
anime matches on normalised title; anything ambiguous or unmatched is counted
and reported separately rather than guessed at. The normalisation mirrors the
one /search/resolve uses in SQL.

Usage:
    python -m pipeline.probe_dub_crosscheck
    python -m pipeline.probe_dub_crosscheck --per-stratum 15 --limit 30
"""

import argparse
import re
import time
from typing import Any
from xml.etree import ElementTree

import httpx

from pipeline.client import RateLimitedClient
from pipeline.probe_dub_coverage import STRATA, fetch_cast, fetch_ids, page_for

ANN_CDN = "https://cdn.animenewsnetwork.com/encyclopedia/api.xml"

# ANN asks for at most one request a second and for caching. This stays well
# clear of that, and the sample is tens of titles rather than thousands.
ANN_MIN_INTERVAL = 2.5
UA = "MangaBrain/0.1 (self-hosted recommendation engine; dub coverage probe)"

# Mirrors api/routers/search.py's NORMALIZE: case-folded, punctuation and
# whitespace dropped, non-ASCII kept so Japanese and Korean titles survive.
NORMALIZE_RE = re.compile(r"[^a-z0-9\u0080-\U0010ffff]+")


def log(message: str) -> None:
    print(f"[crosscheck] {message}", flush=True)


def normalize(title: str | None) -> str:
    return NORMALIZE_RE.sub("", (title or "").lower())


def ann_titles(anime: ElementTree.Element) -> set[str]:
    """Every name ANN knows this entry by, normalised."""
    names = {anime.attrib.get("name", "")}
    for info in anime.iter("info"):
        if (info.attrib.get("type") or "").endswith("title"):
            names.add(info.text or "")
    return {n for n in (normalize(name) for name in names) if n}


def ann_english_cast(anime: ElementTree.Element) -> tuple[int, set[str]]:
    """(English cast entries, all languages the entry is cast in)."""
    langs = {c.attrib.get("lang") for c in anime.iter("cast")}
    english = sum(1 for c in anime.iter("cast") if c.attrib.get("lang") == "EN")
    return english, {lang for lang in langs if lang}


def ann_lookup(
    client: httpx.Client, title: str
) -> tuple[str, ElementTree.Element | None, set[str]]:
    """Resolve one title on ANN. Returns (verdict, entry, languages).

    Verdict is "matched", "ambiguous", "unmatched" or an error label. Only an
    exact normalised match on a single anime entry counts: a near match would
    attribute another show's dub to this one.
    """
    try:
        resp = client.get(ANN_CDN, params={"title": f"~{title}"})
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not fail
        return f"error:{type(exc).__name__}", None, set()
    try:
        root = ElementTree.fromstring(resp.text)
    except ElementTree.ParseError:
        return "error:ParseError", None, set()

    wanted = normalize(title)
    # Anime only: ANN returns <manga> entries for the same search, and a manga
    # has no dub.
    hits = [a for a in root.iter("anime") if wanted in ann_titles(a)]
    if not hits:
        return "unmatched", None, set()
    if len({a.attrib.get("id") for a in hits}) > 1:
        return "ambiguous", None, set()
    english, langs = ann_english_cast(hits[0])
    return ("matched_dubbed" if english else "matched_no_english"), hits[0], langs


def collect_silent(client: RateLimitedClient, per_stratum: int) -> list[tuple[dict[str, Any], int]]:
    """AniList titles that have a cast recorded but no English voice actor.

    A title with no cast at all is excluded: it carries no claim about a dub,
    and in the least popular strata it is every single entry.
    """
    silent: list[tuple[dict[str, Any], int]] = []
    for label, sort, target_rank in STRATA:
        page = page_for(target_rank, per_stratum)
        try:
            media_list = fetch_ids(client, sort, page, per_stratum)
        except Exception as exc:  # noqa: BLE001
            log(f"{label}: {type(exc).__name__}: {exc}")
            continue
        kept = 0
        for media in media_list:
            cast = fetch_cast(client, media["id"])
            if cast.characters and not cast.dubbed:
                silent.append((media, cast.characters))
                kept += 1
        log(f"{label}: {kept}/{len(media_list)} have a cast but no English voice actor")
    return silent


def main() -> None:
    parser = argparse.ArgumentParser(description="Bound AniList's dub false negatives via ANN")
    parser.add_argument("--per-stratum", type=int, default=10)
    parser.add_argument("--limit", type=int, default=30, help="max ANN lookups")
    parser.add_argument("--anilist-min-interval", type=float, default=2.2)
    args = parser.parse_args()

    started = time.time()
    log("=== 1. AniList titles that record a cast but credit no English VA ===")
    anilist = RateLimitedClient(min_interval=args.anilist_min_interval)
    try:
        silent = collect_silent(anilist, args.per_stratum)
    finally:
        anilist.close()
    log(f"collected {len(silent)} silent titles; checking up to {args.limit} against ANN")

    log("=== 2. what ANN says about them ===")
    tally: dict[str, int] = {}
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
        for media, chars in silent[: args.limit]:
            title = media["title"]["romaji"] or media["title"].get("english") or ""
            verdict, entry, langs = ann_lookup(client, title)
            time.sleep(ANN_MIN_INTERVAL)
            tally[verdict] = tally.get(verdict, 0) + 1
            extra = ""
            if entry is not None:
                extra = f" ann={entry.attrib.get('id')} langs={sorted(langs)}"
            log(f"  {verdict:<18} {media['id']:>7} chars={chars:>3} {title[:42]}{extra}")

    log("=== 3. result ===")
    for verdict, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        log(f"  {verdict:<18} {count}")
    matched = tally.get("matched_dubbed", 0) + tally.get("matched_no_english", 0)
    if matched:
        dubbed = tally.get("matched_dubbed", 0)
        log(
            f"false-negative rate: {dubbed}/{matched} "
            f"({100.0 * dubbed / matched:.0f}%) of AniList-silent titles are dubbed per ANN"
        )
    else:
        log("no title resolved on ANN; matching, not coverage, is the blocker")
    unresolved = sum(v for k, v in tally.items() if not k.startswith("matched"))
    log(f"unresolved (unmatched, ambiguous or errored): {unresolved}")
    log(f"done in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
