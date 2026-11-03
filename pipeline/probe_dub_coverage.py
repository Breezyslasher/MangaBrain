"""Measure whether English voice-actor data is a usable dub signal.

AniList has no dub field. What it does have is a cast split by voice-actor
language: CharacterEdge.voiceActors(language: ENGLISH). A raw dump of Cowboy
Bebop confirms the data is really there and really tagged (Wendee Lee, Sherry
Lynn, Beau Billingslea, each languageV2 "English"), so "an English voice actor
is credited" can stand in for "an English dub exists". StaffRoleType.dubGroup
is documented as "Used for grouping roles where multiple dubs exist for the
same language", but it came back null even for Bebop, so at best it
distinguishes competing dubs; it is not the signal itself.

That inference is only worth building on if the data is actually present
across the catalog, and the project's premise makes that a specific question:
a signal that only existed for the top few hundred titles would filter results
down to exactly the popular titles the ranking function refuses to privilege.

So this probe answers three things against the live APIs:

  1. Agreement with ground truth, on a hand-labelled set of titles known to
     be dubbed and known not to be. This is the guard that matters: a first
     version of this probe reported zero English voice actors for every title
     sampled, which read as "the signal does not exist" and was really a bad
     query. Only the labelled set caught it. The labels themselves are
     asserted from memory, so a disagreement is as likely to be a wrong label
     as wrong data; treat it as a prompt to check, not a measurement.
  2. Coverage by popularity stratum, from the most popular titles in the
     catalog down to the least popular.
  3. Disagreement with MAL (via Jikan), which bounds the false-negative rate.
     "No English VA on AniList" is only trustworthy if MAL does not routinely
     know about a dub AniList has not recorded.

One caveat the first run paid for: AniList does not populate a nested
connection across a page of parents, so asking for Page.media.characters
yields empty edges and a silent zero. The cast has to be fetched one title at
a time, which is why this samples tens of titles rather than thousands.

Measured twice, and the rank labels of both runs were wrong: the page number
was hardcoded, so the rank it landed on moved with --per-stratum. The numbers
below are the second run (10 per stratum) with its true ranges, which are
shallower than its labels claimed:

    ranks     1-10      10/10 credit an English voice actor
    ranks   391-400      9/10
    ranks  1191-1200     8/10
    ranks  1981-1990     5/10
    least popular        0/10
    least popular+1000   0/10

Every title in the bottom two strata has no cast recorded at all (music
videos, kids shorts, educational OVAs), so the floor is missing cast data
rather than missing dub data: of the 41 sampled titles with any cast, 32
credit an English voice actor. Nothing here actually sampled rank 5000; the
deepest stratum of real shows was ranks 1981-1990, at 50%. dubGroup is
populated occasionally and usefully ("4Kids" and "Odex" on One Piece,
"Animax" on K-ON!!).

The false negatives cluster in sequel, season and OVA entries whose parent
series is dubbed (World Trigger 3rd Season: 25 characters, 0 English voice
actors; Eromanga Sensei OVA; High School DxD OVA), which looks like cast
credits not being re-entered per entry rather than a dub not existing. A
franchise-aware fill via media_relations would recover most of them.

The asymmetry this implies matters more than the headline number. "Credits an
English VA" is a reliable positive: it is hard to get there by accident.
"Credits none" is not a reliable negative in the midlist, where Berserk 2
(2017) came back with 21 characters and zero English voice actors despite
having a dub. So this supports a "known dubbed" filter, not a "sub only" one.

Run it where the network allows (CI, or the NAS):

    python -m pipeline.probe_dub_coverage
    python -m pipeline.probe_dub_coverage --per-stratum 5 --cross-check 10
    python -m pipeline.probe_dub_coverage --debug-id 1    # raw cast JSON
"""

import argparse
import json
import time
from typing import Any

from pipeline.client import RateLimitedClient

ANILIST_URL = "https://graphql.anilist.co"
JIKAN_BASE = "https://api.jikan.moe/v4"

CHARACTERS_PER_PAGE = 25

# (label, sort, target_rank): the popularity rank each stratum should land on.
# The page is derived from --per-stratum rather than hardcoded, because the
# same page number means a different rank at a different page size. Hardcoding
# the page and then labelling it by rank is how an earlier run reported
# "rank ~5000" while actually sampling ranks 1981-1990.
#
# Deep POPULARITY_DESC pagination starts returning HTTP 400, so the tail is
# reached by sorting ascending from the other end rather than paging thousands
# deep. POPULARITY ascending rank 1 is the least popular title in the catalog,
# which is exactly the hidden-gem case.
STRATA: tuple[tuple[str, str, int], ...] = (
    ("most popular", "POPULARITY_DESC", 1),
    ("rank ~1000", "POPULARITY_DESC", 1000),
    ("rank ~3000", "POPULARITY_DESC", 3000),
    ("rank ~5000", "POPULARITY_DESC", 5000),
    ("least popular", "POPULARITY", 1),
    ("least popular +1000", "POPULARITY", 1000),
)


def page_for(target_rank: int, per_page: int) -> int:
    """The page whose last entry sits at or just past target_rank."""
    return max(1, -(-target_rank // per_page))


# Hand-labelled ground truth. "dub" means an English dub is known to exist and
# be widely documented; "no dub" means none is known. Kept small on purpose:
# every entry is checked by hand, and it is the only thing standing between a
# broken query and a confident wrong answer.
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

IDS_QUERY = """
query ($page: Int, $perPage: Int, $sort: [MediaSort]) {
  Page(page: $page, perPage: $perPage) {
    media(type: ANIME, sort: $sort) {
      id
      idMal
      popularity
      format
      startDate { year }
      title { romaji }
    }
  }
}
"""

# Per title: a nested connection is only populated for a single parent, so
# this is one request per title and the sample sizes reflect that.
CAST_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    characters(perPage: CHARACTERS_PER_PAGE, sort: ROLE) {
      edges {
        # node is load-bearing: without it AniList resolves voiceActors to
        # null on every edge instead of erroring, which silently reads as
        # "no English cast" for every title in the catalog.
        node { id }
        voiceActors(language: ENGLISH) { id }
        voiceActorRoles(language: ENGLISH) { dubGroup }
      }
    }
  }
}
""".replace("CHARACTERS_PER_PAGE", str(CHARACTERS_PER_PAGE))

DEBUG_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji }
    characters(perPage: 3, sort: ROLE) {
      pageInfo { total }
      edges {
        node { id name { full } }
        voiceActors { id language: languageV2 name { full } }
        voiceActorsEnglish: voiceActors(language: ENGLISH) { id name { full } }
        voiceActorRoles(language: ENGLISH) {
          dubGroup
          voiceActor { id name { full } }
        }
      }
    }
  }
}
"""


class Cast:
    """What AniList records about one title's cast."""

    def __init__(self, characters: int, english: int, groups: set[str]) -> None:
        # characters is how many cast edges came back, capped by
        # CHARACTERS_PER_PAGE. pageInfo.total is not usable here: AniList
        # reported a flat 500 for titles with a handful of characters.
        self.characters = characters
        self.english = english
        self.groups = groups

    @property
    def dubbed(self) -> bool:
        return self.english > 0


def log(message: str) -> None:
    print(f"[dub-probe] {message}", flush=True)


def anilist(client: RateLimitedClient, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    data = client.request("POST", ANILIST_URL, json_body={"query": query, "variables": variables})
    if data.get("errors"):
        raise RuntimeError(f"AniList errors: {data['errors']}")
    return data["data"]


def fetch_cast(client: RateLimitedClient, media_id: int) -> Cast:
    connection = anilist(client, CAST_QUERY, {"id": media_id})["Media"]["characters"]
    actors: set[int] = set()
    groups: set[str] = set()
    for edge in connection["edges"]:
        if edge.get("voiceActors") is None and edge.get("node"):
            raise RuntimeError("voiceActors resolved to null; the query lost its node selection")
        for actor in edge.get("voiceActors") or []:
            actors.add(actor["id"])
        for role in edge.get("voiceActorRoles") or []:
            if role.get("dubGroup"):
                groups.add(role["dubGroup"])
    return Cast(len(connection["edges"]), len(actors), groups)


def fetch_ids(
    client: RateLimitedClient, sort: str, page: int, per_page: int
) -> list[dict[str, Any]]:
    variables = {"page": page, "perPage": per_page, "sort": [sort]}
    return anilist(client, IDS_QUERY, variables)["Page"]["media"]


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


def describe(media: dict[str, Any], cast: Cast) -> str:
    year = (media["startDate"] or {}).get("year") or 0
    groups = f" groups={sorted(cast.groups)}" if cast.groups else ""
    return (
        f"  {media['id']:>7} pop={media['popularity'] or 0:>7} {year:>4} "
        f"{media['format'] or '-':<8} chars={cast.characters:>3} "
        f"en_va={cast.english:>3}{groups}  {(media['title']['romaji'] or '')[:40]}"
    )


def run_debug(client: RateLimitedClient, media_id: int) -> None:
    """Print both query shapes for one title, side by side.

    DEBUG_QUERY demonstrably returns English voice actors; CAST_QUERY returns
    none for the same titles. Since one of the two is lying about the same
    data, print both raw responses rather than reason about the difference.
    """
    for name, query in (("CAST_QUERY", CAST_QUERY), ("DEBUG_QUERY", DEBUG_QUERY)):
        log(f"=== {name} raw response for media {media_id} ===")
        data = client.request(
            "POST", ANILIST_URL, json_body={"query": query, "variables": {"id": media_id}}
        )
        print(json.dumps(data, indent=2)[:7000], flush=True)


def run_ground_truth(client: RateLimitedClient) -> int:
    log("=== 1. agreement with hand-labelled ground truth ===")
    agree = 0
    for media_id, (name, expected) in GROUND_TRUTH.items():
        cast = fetch_cast(client, media_id)
        ok = cast.dubbed == expected
        agree += ok
        log(
            f"  {name[:28]:<28} labelled={'dub' if expected else 'no dub':<6} "
            f"chars={cast.characters:>3} en_va={cast.english:>3} "
            f"-> {'agree' if ok else 'DISAGREE'}"
        )
    log(f"ground truth: {agree}/{len(GROUND_TRUTH)} agree")
    return agree


def run_strata(client: RateLimitedClient, per_stratum: int) -> list[dict[str, Any]]:
    log("=== 2. coverage by popularity stratum ===")
    sampled: list[dict[str, Any]] = []
    for label, sort, target_rank in STRATA:
        page = page_for(target_rank, per_stratum)
        lo = (page - 1) * per_stratum + 1
        # The label says what was asked for; the range says what was sampled.
        scope = f"{label} (ranks {lo}-{lo + per_stratum - 1}"
        scope += " from the bottom)" if sort == "POPULARITY" else ")"
        try:
            media_list = fetch_ids(client, sort, page, per_stratum)
        except Exception as exc:  # noqa: BLE001 - a probe reports, it does not fail
            log(f"{scope}: {type(exc).__name__}: {exc}")
            continue
        if not media_list:
            log(f"{scope}: empty")
            continue
        hits = 0
        for media in media_list:
            cast = fetch_cast(client, media["id"])
            media["cast"] = cast
            hits += cast.dubbed
            sampled.append(media)
            log(describe(media, cast))
        pct = 100.0 * hits / len(media_list)
        log(f"{scope}: {hits}/{len(media_list)} ({pct:.0f}%) credit an English voice actor")
    return sampled


def run_cross_check(client: RateLimitedClient, sampled: list[dict[str, Any]], limit: int) -> None:
    log("=== 3. disagreement with MAL (bounds AniList false negatives) ===")
    # Only titles where AniList credits nobody can reveal a false negative, so
    # spend the Jikan budget there rather than on confirmed dubs.
    silent = [m for m in sampled if not m["cast"].dubbed and m["idMal"]]
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
                f"{(media['title']['romaji'] or '')[:40]}"
            )
    if not checked:
        log("cross-check: nothing to check")
        return
    log(
        f"cross-check: {missed}/{checked} titles AniList calls undubbed have an "
        f"English VA on MAL ({100.0 * missed / checked:.0f}% false negative)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure English voice-actor coverage")
    parser.add_argument("--per-stratum", type=int, default=10)
    parser.add_argument(
        "--cross-check",
        type=int,
        default=15,
        help="how many AniList-silent titles to re-check against MAL (0 to skip)",
    )
    parser.add_argument(
        "--debug-id",
        type=int,
        default=0,
        help="dump the raw character/VA JSON for one media id and stop",
    )
    parser.add_argument("--anilist-min-interval", type=float, default=2.2)
    parser.add_argument("--jikan-min-interval", type=float, default=1.2)
    args = parser.parse_args()

    started = time.time()
    sampled: list[dict[str, Any]] = []
    # No cache_dir: a coverage measurement must see live data, not a replay.
    client = RateLimitedClient(min_interval=args.anilist_min_interval)
    try:
        if args.debug_id:
            run_debug(client, args.debug_id)
            return
        run_ground_truth(client)
        sampled = run_strata(client, args.per_stratum)
    finally:
        client.close()

    hits = sum(1 for m in sampled if m["cast"].dubbed)
    no_cast = sum(1 for m in sampled if not m["cast"].characters)
    if sampled:
        log(
            f"overall sampled coverage: {hits}/{len(sampled)} "
            f"({100.0 * hits / len(sampled):.0f}%) credit an English voice actor; "
            f"{no_cast} have no cast recorded at all"
        )

    if args.cross_check and sampled:
        jikan = RateLimitedClient(min_interval=args.jikan_min_interval, max_retries=1)
        try:
            run_cross_check(jikan, sampled, args.cross_check)
        finally:
            jikan.close()

    log(f"done in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
