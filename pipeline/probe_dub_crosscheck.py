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
import time
from typing import Any

import httpx

from pipeline.ann import (
    build_index,
    fetch_anime_index,
    fetch_casts,
    match,
    normalize,
)
from pipeline.client import RateLimitedClient
from pipeline.probe_dub_coverage import STRATA, fetch_cast, fetch_ids, page_for

UA = "MangaBrain/0.1 (self-hosted recommendation engine; dub coverage probe)"

# ANN's search needed the query to be a prefix of a real title and returned
# nothing for sequels, OVAs and Japanese script, so matching is done offline
# against the bulk report instead. Names tried per title are still capped.
MAX_TITLE_TRIES = 4
MAX_SYNONYM_TRIES = 2


def log(message: str) -> None:
    print(f"[crosscheck] {message}", flush=True)


def candidate_titles(media: dict[str, Any]) -> list[str]:
    """Every name worth trying on ANN, best bet first.

    ANN is an English-language encyclopedia and files entries under their
    release title, so the English name resolves where romaji does not:
    "Shuumatsu no Harem" is filed as "World's End Harem". Romaji and native
    still follow, because plenty of entries have no distinct English name -
    capeta and Shadowverse both resolved on romaji alone.
    """
    title = media.get("title") or {}
    ordered = [title.get("english"), title.get("romaji"), title.get("native")]
    ordered += (media.get("synonyms") or [])[:MAX_SYNONYM_TRIES]
    seen: set[str] = set()
    out: list[str] = []
    for name in ordered:
        key = normalize(name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def resolve_media(index: dict, media: dict[str, Any]) -> tuple[str, Any]:
    """Match one AniList entry against the local ANN index."""
    year = (media.get("startDate") or {}).get("year")
    return match(
        index,
        candidate_titles(media)[:MAX_TITLE_TRIES],
        year=year,
        fmt=media.get("format"),
    )


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

    log("=== 2. matching against ANN's bulk anime index ===")
    tally: dict[str, int] = {}
    resolved: list[tuple[dict[str, Any], int, Any]] = []
    with httpx.Client(timeout=90.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
        entries = fetch_anime_index(client)
        index = build_index(entries)
        log(f"index: {len(entries)} ANN anime entries, {len(index)} distinct keys")

        for media, chars in silent[: args.limit]:
            verdict, entry = resolve_media(index, media)
            tally[verdict] = tally.get(verdict, 0) + 1
            shown = (media["title"]["romaji"] or "")[:40]
            extra = f" ann={entry.ann_id} {entry.name[:30]!r}" if entry else ""
            log(f"  {verdict:<18} {media['id']:>7} chars={chars:>3} {shown}{extra}")
            if entry is not None:
                resolved.append((media, chars, entry))

        log("=== 3. what ANN says about the ones that resolved ===")
        casts = fetch_casts(client, [e.ann_id for _, _, e in resolved])

    dubbed = 0
    for media, chars, entry in resolved:
        english, langs = casts.get(entry.ann_id, (0, set()))
        # chars is AniList's cast size, printed so a zero there is visible.
        dubbed += english > 0
        verdict = "DUBBED per ANN" if english else "no English cast"
        log(
            f"  {verdict:<16} {media['id']:>7} ann={entry.ann_id} anilist_chars={chars:>3} "
            f"en={english:>3} langs={sorted(langs)} {(media['title']['romaji'] or '')[:34]}"
        )

    log("=== 4. result ===")
    for verdict, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        log(f"  {verdict:<18} {count}")
    if resolved:
        log(
            f"false-negative rate: {dubbed}/{len(resolved)} "
            f"({100.0 * dubbed / len(resolved):.0f}%) of AniList-silent titles are "
            "dubbed per ANN"
        )
    else:
        log("nothing resolved; matching, not coverage, is still the blocker")
    unresolved = sum(v for k, v in tally.items() if k.startswith(("unmatched", "ambiguous")))
    log(f"unresolved (unmatched or ambiguous): {unresolved}")
    log(f"done in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
