"""Can English dubs be inferred for titles with no cast recorded at all?

The gap is specific and large. 8,650 anime - 38.2% of the catalog - have no
cast recorded on AniList whatsoever, not merely no English cast. For those,
every source tried so far is silent by construction: AniList has no voice
actors to report, ANN mostly has no entry (it holds 12,882 against 22,667),
and franchise propagation only helps a title with a dubbed relative.

Two signals sit in AniList's own schema and have never been fetched, so they
cost nothing beyond widening the sync query:

  1. studios. English dub houses are credited as studios on AniList for some
     titles - Bang Zoom! Entertainment, Studiopolis, NYAV Post, Sound Cadence
     - and a dub house in the credits is direct evidence of a dub, cast rows
     or not.
  2. externalLinks. A Funimation, HIDIVE or Crunchyroll link is weaker but
     still informative, since those platforms dub, and the field carries a
     `language` of its own.

The experiment is whether either DISCRIMINATES, not whether it appears. A
studio that shows up on dubbed and undubbed titles alike predicts nothing. So
titles are split by whether they actually have an English cast, and each
signal is scored on both halves - a signal is only useful if it is common in
one and rare in the other.

Then the number that matters: among titles with NO cast at all, how many
carry a signal? That is the share of the 38.2% gap this could fill, and it
is the only figure that justifies widening the sync.
"""

import argparse
import json
import random
import time
from collections import Counter

import httpx

URL = "https://graphql.anilist.co"

# Companies whose business is English dub production. Treated as a hypothesis
# to be scored, not as an answer: the probe reports how well they separate
# dubbed from undubbed titles rather than assuming they do.
DUB_STUDIO_HINTS = (
    "bang zoom",
    "studiopolis",
    "nyav post",
    "sound cadence",
    "funimation",
    "sentai",
    "ocean productions",
    "ocean group",
    "bluewater",
    "new generation pictures",
    "seraphim digital",
    "vsi",
    "iyuno",
    "dubbing brothers",
    "sdi media",
    "post haste digital",
    "chiptune",
    "discotek",
)

# Platforms that commission or carry dubs.
DUB_LINK_HINTS = ("funimation", "hidive", "crunchyroll", "netflix", "hulu", "amazon", "vrv")

QUERY = """
query ($ids: [Int], $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(id_in: $ids, type: ANIME) {
      id
      title { romaji }
      countryOfOrigin
      studios { edges { isMain node { id name } } }
      externalLinks { site language }
      characters(perPage: 25, sort: ROLE) {
        edges {
          node { id }
          voiceActors { id languageV2 }
        }
      }
    }
  }
}
"""

IDS_QUERY = """
query ($ids: [Int]) {
  Page(page: 1, perPage: 50) {
    media(id_in: $ids, type: ANIME) { id }
  }
}
"""


def post(client: httpx.Client, query: str, variables: dict) -> dict:
    resp = client.post(URL, json={"query": query, "variables": variables})
    payload = resp.json()
    if payload.get("errors"):
        print(f"   errors: {json.dumps(payload['errors'])[:240]}", flush=True)
    return payload


def sample_ids(client: httpx.Client, want: int, seed: int = 20261103) -> list[int]:
    """Random ids across the whole id space, not the popular head.

    A popularity-sorted sample cannot reach past AniList's 5000-row offset
    cap, and dub coverage collapses in the tail - which is how an earlier
    estimate came out at three times the measured rate.
    """
    rng = random.Random(seed)
    out: list[int] = []
    while len(out) < want:
        ids = [rng.randint(1, 190_000) for _ in range(50)]
        payload = post(client, IDS_QUERY, {"ids": ids})
        media = ((payload.get("data") or {}).get("Page") or {}).get("media") or []
        out.extend(m["id"] for m in media)
        time.sleep(2.0)
    return out[:want]


def english_cast(media: dict) -> int:
    actors = set()
    for edge in (media.get("characters") or {}).get("edges") or []:
        for actor in edge.get("voiceActors") or []:
            if (actor.get("languageV2") or "").upper() == "ENGLISH":
                actors.add(actor["id"])
    return len(actors)


def any_cast(media: dict) -> int:
    actors = set()
    for edge in (media.get("characters") or {}).get("edges") or []:
        for actor in edge.get("voiceActors") or []:
            actors.add(actor["id"])
    return len(actors)


def studio_names(media: dict) -> list[str]:
    return [
        (e.get("node") or {}).get("name") or ""
        for e in ((media.get("studios") or {}).get("edges") or [])
    ]


def link_sites(media: dict) -> list[str]:
    return [(link.get("site") or "") for link in media.get("externalLinks") or []]


def matches(names: list[str], hints: tuple[str, ...]) -> list[str]:
    hit = []
    for name in names:
        low = name.lower()
        for hint in hints:
            if hint in low:
                hit.append(name)
                break
    return hit


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe inferable English dub signals")
    parser.add_argument("--sample", type=int, default=300)
    args = parser.parse_args()

    with httpx.Client(timeout=120.0, headers={"Content-Type": "application/json"}) as client:
        ids = sample_ids(client, args.sample)
        print(f"sampled {len(ids)} random anime ids across the whole id space\n", flush=True)

        records = []
        for start in range(0, len(ids), 50):
            chunk = ids[start : start + 50]
            payload = post(client, QUERY, {"ids": chunk, "perPage": len(chunk)})
            page = (payload.get("data") or {}).get("Page") or {}
            records.extend(page.get("media") or [])
            time.sleep(2.0)
        print(f"fetched {len(records)} records\n", flush=True)

        dubbed = [m for m in records if english_cast(m) > 0]
        no_cast = [m for m in records if any_cast(m) == 0]
        cast_no_en = [m for m in records if any_cast(m) > 0 and english_cast(m) == 0]

        print("=" * 72)
        print("1. the population")
        print("=" * 72)
        n = len(records) or 1
        print(f"   has an English cast        {len(dubbed):>4}/{n}  ({100 * len(dubbed) / n:.1f}%)")
        print(
            f"   has a cast, none English   {len(cast_no_en):>4}/{n}"
            f"  ({100 * len(cast_no_en) / n:.1f}%)"
        )
        print(
            f"   NO cast recorded at all    {len(no_cast):>4}/{n}"
            f"  ({100 * len(no_cast) / n:.1f}%)   <- the gap"
        )

        print()
        print("=" * 72)
        print("2. does a dub-studio credit DISCRIMINATE?")
        print("=" * 72)
        for label, group in (("dubbed", dubbed), ("cast, no English", cast_no_en)):
            if not group:
                continue
            hit = sum(1 for m in group if matches(studio_names(m), DUB_STUDIO_HINTS))
            print(
                f"   {label:<20} {hit:>4}/{len(group)}"
                f"  ({100 * hit / len(group):.1f}%) carry a dub-studio credit",
                flush=True,
            )
        print(
            "   A signal is only useful if that first rate is high and the"
            "\n   second is low. Similar rates mean it predicts nothing.",
            flush=True,
        )

        print()
        print("=" * 72)
        print("3. does a dub-platform link discriminate?")
        print("=" * 72)
        for label, group in (("dubbed", dubbed), ("cast, no English", cast_no_en)):
            if not group:
                continue
            hit = sum(1 for m in group if matches(link_sites(m), DUB_LINK_HINTS))
            print(
                f"   {label:<20} {hit:>4}/{len(group)}"
                f"  ({100 * hit / len(group):.1f}%) carry a dub-platform link",
                flush=True,
            )

        print()
        print("=" * 72)
        print("4. the payoff: how much of the no-cast gap carries a signal?")
        print("=" * 72)
        if no_cast:
            s_hit = sum(1 for m in no_cast if matches(studio_names(m), DUB_STUDIO_HINTS))
            l_hit = sum(1 for m in no_cast if matches(link_sites(m), DUB_LINK_HINTS))
            either = sum(
                1
                for m in no_cast
                if matches(studio_names(m), DUB_STUDIO_HINTS)
                or matches(link_sites(m), DUB_LINK_HINTS)
            )
            total = len(no_cast)
            for label, hit in (
                ("dub-studio credit", s_hit),
                ("dub-platform link", l_hit),
                ("either", either),
            ):
                print(f"   {label:<20} {hit:>4}/{total}  ({100 * hit / total:.1f}%)", flush=True)
        else:
            print("   no no-cast titles in this sample", flush=True)

        print()
        print("=" * 72)
        print("5. what the data actually contains, for hypothesis repair")
        print("=" * 72)
        studios: Counter = Counter()
        for m in dubbed:
            studios.update(studio_names(m))
        print("   studios most common on titles WITH an English cast:", flush=True)
        for name, count in studios.most_common(20):
            print(f"     {name[:44]:<44} {count:>4}", flush=True)
        sites: Counter = Counter()
        for m in dubbed:
            sites.update(link_sites(m))
        print("   link sites most common on titles WITH an English cast:", flush=True)
        for name, count in sites.most_common(15):
            print(f"     {name[:44]:<44} {count:>4}", flush=True)


if __name__ == "__main__":
    main()
