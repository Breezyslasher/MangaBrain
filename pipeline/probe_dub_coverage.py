"""Measure whether English voice-actor data is a usable dub signal.

AniList has no dub field. What it does have is a cast broken down by voice
actor language: CharacterEdge.voiceActors(language: ENGLISH) and
voiceActorRoles(language: ENGLISH), whose StaffRoleType carries dubGroup,
documented as "Used for grouping roles where multiple dubs exist for the same
language." So "an English VA is credited" stands in for "an English dub
exists". That inference is only worth building on if the data is actually
there, and the project's whole premise is that obscure titles must be
first-class, so the question is not whether the signal exists for Cowboy
Bebop but how far down the tail it survives.

This probe answers three things against the live APIs:

  1. Agreement with ground truth, on a hand-labelled set of titles that are
     known to be dubbed and known not to be.
  2. Coverage by popularity stratum, which is the hidden-gem question: a
     signal present only in the top 1000 would filter the catalog down to
     exactly the popular titles the ranking refuses to privilege.
  3. Disagreement with MAL (via Jikan), which bounds the false-negative rate.
     AniList saying "no English VA" is only trustworthy if MAL does not
     routinely know about a dub AniList has not recorded.

Run it where the network allows (CI, or the NAS):

    python -m pipeline.probe_dub_coverage
    python -m pipeline.probe_dub_coverage --strata 1,40,120 --cross-check 20
"""

import argparse
import time
from typing import Any

from pipeline.client import RateLimitedClient

ANILIST_URL = "https://graphql.anilist.co"
JIKAN_BASE = "https://api.jikan.moe/v4"

# Popularity-ranked pages (perPage=25) to sample. Page 1 is the 25 most
# popular titles; page 700 is around rank 17,500, near the end of the catalog.
DEFAULT_STRATA = (1, 40, 120, 280, 480, 700)
PER_PAGE = 25
CHARACTERS_PER_PAGE = 25

# Hand-labelled ground truth. "Dubbed" means an English dub is known to exist
# and be widely documented; "not dubbed" means no English dub is known as of
# the labelling date. Kept small on purpose: every entry is checked by hand.
GROUND_TRUTH: dict[int, tuple[str, bool]] = {
    1: ("Cowboy Bebop", True),
    21: ("One Piece", True),
    1535: ("Death Note", True),
    10087: ("Fate/Zero", True),
    113415: ("Jujutsu Kaisen", True),
    154587: ("Sousou no Frieren", True),
    114129: ("Horimiya", True),
    20954: ("Koe no Katachi", True),
    97938: ("Kemono Friends", False),
    127549: ("Kageki Shoujo!!", False),
    108268: ("Yakunara Mug Cup mo", False),
    125367: ("Mewkledreamy Mix!", False),
}

# GraphQL is full of braces, so these are composed by token replacement
# rather than f-strings or percent formatting.
MEDIA_FIELDS = """
      id
      idMal
      popularity
      format
      startDate { year }
      title { romaji }
      characters(perPage: CHARACTERS_PER_PAGE, sort: ROLE) {
        edges {
          voiceActorRoles(language: ENGLISH) {
            dubGroup
            voiceActor { id }
          }
        }
      }
""".replace("CHARACTERS_PER_PAGE", str(CHARACTERS_PER_PAGE))

BY_IDS_QUERY = """
query ($ids: [Int], $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(id_in: $ids, type: ANIME) {
MEDIA_FIELDS    }
  }
}
""".replace("MEDIA_FIELDS", MEDIA_FIELDS)

BY_PAGE_QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    media(type: ANIME, sort: POPULARITY_DESC) {
MEDIA_FIELDS    }
  }
}
""".replace("MEDIA_FIELDS", MEDIA_FIELDS)


def log(message: str) -> None:
    print(f"[dub-probe] {message}", flush=True)


def fetch(client: RateLimitedClient, query: str, variables: dict[str, Any]) -> list[dict[str, Any]]:
    data = client.request("POST", ANILIST_URL, json_body={"query": query, "variables": variables})
    if data.get("errors"):
        raise RuntimeError(f"AniList errors: {data['errors']}")
    return data["data"]["Page"]["media"]


def english_cast(media: dict[str, Any]) -> tuple[int, set[str]]:
    """Credited English voice actors, and any dub groups named for them."""
    actors: set[int] = set()
    groups: set[str] = set()
    for edge in media["characters"]["edges"]:
        for role in edge.get("voiceActorRoles") or []:
            actor = (role.get("voiceActor") or {}).get("id")
            if actor is not None:
                actors.add(actor)
            if role.get("dubGroup"):
                groups.add(role["dubGroup"])
    return len(actors), groups


def jikan_has_english_va(client: RateLimitedClient, id_mal: int) -> bool | None:
    """Whether MAL credits an English voice actor. None if MAL cannot say."""
    try:
        data = client.request("GET", f"{JIKAN_BASE}/anime/{id_mal}/characters")
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not fail
        log(f"  jikan {id_mal}: {type(exc).__name__}")
        return None
    for entry in data.get("data") or []:
        for actor in entry.get("voice_actors") or []:
            if (actor.get("language") or "").lower() == "english":
                return True
    return False


def report_stratum(media_list: list[dict[str, Any]], label: str) -> tuple[int, int]:
    hits = 0
    for media in media_list:
        count, groups = english_cast(media)
        hits += bool(count)
        year = (media["startDate"] or {}).get("year") or 0
        group_note = f" groups={sorted(groups)}" if groups else ""
        log(
            f"  {media['id']:>7} pop={media['popularity'] or 0:>7} {year:>4} "
            f"{media['format'] or '-':<8} en_va={count:>3}{group_note}  "
            f"{(media['title']['romaji'] or '')[:44]}"
        )
    total = len(media_list)
    pct = 100.0 * hits / total if total else 0.0
    log(f"{label}: {hits}/{total} ({pct:.0f}%) credit an English voice actor")
    return hits, total


def run_ground_truth(client: RateLimitedClient) -> None:
    log("=== 1. agreement with hand-labelled ground truth ===")
    media_list = fetch(client, BY_IDS_QUERY, {"ids": list(GROUND_TRUTH), "perPage": PER_PAGE})
    agree = 0
    for media in media_list:
        name, expected = GROUND_TRUTH[media["id"]]
        count, _ = english_cast(media)
        predicted = bool(count)
        ok = predicted == expected
        agree += ok
        log(
            f"  {name[:28]:<28} labelled={'dub' if expected else 'no dub':<6} "
            f"en_va={count:>3} -> {'agree' if ok else 'DISAGREE'}"
        )
    log(f"ground truth: {agree}/{len(media_list)} agree")


def run_strata(client: RateLimitedClient, strata: tuple[int, ...]) -> list[dict[str, Any]]:
    log("=== 2. coverage by popularity stratum ===")
    sampled: list[dict[str, Any]] = []
    for page in strata:
        media_list = fetch(client, BY_PAGE_QUERY, {"page": page, "perPage": PER_PAGE})
        if not media_list:
            log(f"page {page}: empty (past the end of the catalog)")
            continue
        rank_lo = (page - 1) * PER_PAGE + 1
        report_stratum(media_list, f"rank ~{rank_lo}-{rank_lo + len(media_list) - 1}")
        sampled.extend(media_list)
    return sampled


def run_cross_check(client: RateLimitedClient, sampled: list[dict[str, Any]], limit: int) -> None:
    log("=== 3. disagreement with MAL (bounds AniList false negatives) ===")
    # Only titles where AniList credits nobody can reveal a false negative,
    # so spend the Jikan budget there rather than on confirmed dubs.
    silent = [m for m in sampled if not english_cast(m)[0] and m["idMal"]]
    checked = missed = 0
    for media in silent[:limit]:
        verdict = jikan_has_english_va(client, media["idMal"])
        if verdict is None:
            continue
        checked += 1
        missed += verdict
        if verdict:
            log(
                f"  MISSED {media['id']:>7} mal={media['idMal']:>7} "
                f"{(media['title']['romaji'] or '')[:44]}"
            )
    if not checked:
        log("cross-check: nothing to check")
        return
    log(
        f"cross-check: {missed}/{checked} titles AniList calls undubbed have an "
        f"English VA on MAL ({100.0 * missed / checked:.0f}% false negative)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strata",
        default=",".join(str(p) for p in DEFAULT_STRATA),
        help="comma-separated popularity-ranked page numbers to sample",
    )
    parser.add_argument(
        "--cross-check",
        type=int,
        default=40,
        help="how many AniList-silent titles to re-check against MAL (0 to skip)",
    )
    parser.add_argument("--anilist-min-interval", type=float, default=2.5)
    parser.add_argument("--jikan-min-interval", type=float, default=1.0)
    args = parser.parse_args()

    strata = tuple(int(p) for p in args.strata.split(",") if p.strip())
    started = time.time()

    # No cache_dir: a coverage measurement must see live data, not a replay.
    anilist = RateLimitedClient(min_interval=args.anilist_min_interval)
    try:
        run_ground_truth(anilist)
        sampled = run_strata(anilist, strata)
    finally:
        anilist.close()

    hits = sum(1 for m in sampled if english_cast(m)[0])
    total = len(sampled)
    if total:
        log(f"overall sampled coverage: {hits}/{total} ({100.0 * hits / total:.0f}%)")

    if args.cross_check and sampled:
        jikan = RateLimitedClient(min_interval=args.jikan_min_interval, max_retries=2)
        try:
            run_cross_check(jikan, sampled, args.cross_check)
        finally:
            jikan.close()

    log(f"done in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
