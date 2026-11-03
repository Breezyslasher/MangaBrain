"""Which studios actually predict an English dub?

The hand-written hint list was guesswork and demonstrably incomplete: it
caught Funimation and Sentai but missed The Right Stuf International and Viz
Media, both of which turned up on dubbed titles in the sample that tested it.
Guessing which companies dub is how a signal ends up measuring my knowledge
of the industry rather than the data.

So the list is derived instead. Every studio is scored on how much more often
it appears on a title with a recorded English cast than the base rate - its
lift - with the support behind each number reported, because a studio seen
twice tells you nothing however extreme its rate looks.

The target is not the no-cast gap. That was measured and the signal is absent
there: 3.1% of no-cast titles carry any studio or link hint, about 302 titles
or 1.3% of the catalog, because those entries have no metadata of any kind.
The target is AniList's FALSE NEGATIVES - the ~8,765 titles that do have a
cast recorded but no English cast, which is the Berserk 2017 case: 21
characters, no English voice actors, and a dub that exists. A dub house in
the credits is direct evidence the cast data missed one.

Two cautions this probe cannot resolve, and does not pretend to:

  - The label is itself incomplete. "Has a recorded English cast" is the only
    available ground truth and is exactly the thing being corrected, so lift
    is measured against recorded dubs, not real ones. That biases toward
    studios whose titles get their casts entered.
  - Appearing because a title is dubbed is the point, not circularity. The
    inference runs the other way: from a credit to a dub, on titles whose
    cast rows are missing.
"""

import argparse
import json
import math
import random
import time
from collections import Counter

import httpx

URL = "https://graphql.anilist.co"

QUERY = """
query ($ids: [Int], $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(id_in: $ids, type: ANIME) {
      id
      studios { edges { isMain node { name } } }
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


def sample_ids(client: httpx.Client, want: int, seed: int) -> list[int]:
    """Random across the whole id space; a popularity sample cannot reach the
    tail past AniList's 5000-row offset cap, and the tail is the subject."""
    rng = random.Random(seed)
    out: list[int] = []
    while len(out) < want:
        payload = post(client, IDS_QUERY, {"ids": [rng.randint(1, 190_000) for _ in range(50)]})
        media = ((payload.get("data") or {}).get("Page") or {}).get("media") or []
        out.extend(m["id"] for m in media)
        time.sleep(2.0)
    return out[:want]


def has_english(media: dict) -> bool:
    for edge in (media.get("characters") or {}).get("edges") or []:
        for actor in edge.get("voiceActors") or []:
            if (actor.get("languageV2") or "").upper() == "ENGLISH":
                return True
    return False


def has_any_cast(media: dict) -> bool:
    for edge in (media.get("characters") or {}).get("edges") or []:
        if edge.get("voiceActors"):
            return True
    return False


def studios(media: dict) -> list[str]:
    out = []
    for edge in (media.get("studios") or {}).get("edges") or []:
        name = (edge.get("node") or {}).get("name")
        if name:
            out.append(name)
    return out


def wilson_lower(hits: int, total: int, z: float = 1.96) -> float:
    """Lower bound of the 95% interval, so small samples cannot top the list.

    A studio seen twice with both titles dubbed has a point estimate of 100%
    and tells you nothing. The bound is what makes support and rate
    comparable in one number.
    """
    if total == 0:
        return 0.0
    p = hits / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denom)


def main() -> None:
    parser = argparse.ArgumentParser(description="Derive which studios predict a dub")
    parser.add_argument("--sample", type=int, default=1000)
    parser.add_argument("--min-support", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261103)
    args = parser.parse_args()

    with httpx.Client(timeout=120.0, headers={"Content-Type": "application/json"}) as client:
        ids = sample_ids(client, args.sample, args.seed)
        print(f"sampled {len(ids)} random anime ids\n", flush=True)
        records = []
        for start in range(0, len(ids), 50):
            chunk = ids[start : start + 50]
            payload = post(client, QUERY, {"ids": chunk, "perPage": len(chunk)})
            page = (payload.get("data") or {}).get("Page") or {}
            records.extend(page.get("media") or [])
            time.sleep(2.0)

    with_cast = [m for m in records if has_any_cast(m)]
    dubbed = [m for m in with_cast if has_english(m)]
    base = len(dubbed) / len(with_cast) if with_cast else 0.0

    print("=" * 72)
    print("population")
    print("=" * 72)
    print(f"   records fetched            {len(records)}")
    print(f"   with any cast recorded     {len(with_cast)}")
    print(f"   of those, English cast     {len(dubbed)}  -> base rate {base * 100:.1f}%")
    print(
        "\n   Lift is measured against that base rate, over titles WITH a cast"
        "\n   only: a studio cannot be credited with predicting a dub on a"
        "\n   title where nothing was recorded either way.",
        flush=True,
    )

    seen: Counter = Counter()
    dubbed_with: Counter = Counter()
    for media in with_cast:
        names = set(studios(media))
        seen.update(names)
        if has_english(media):
            dubbed_with.update(names)

    rows = []
    for name, support in seen.items():
        if support < args.min_support:
            continue
        hits = dubbed_with[name]
        rate = hits / support
        rows.append((wilson_lower(hits, support), rate, hits, support, name))
    rows.sort(reverse=True)

    print()
    print("=" * 72)
    print(f"studios with support >= {args.min_support}, ranked by 95% lower bound")
    print("=" * 72)
    print(f"   {'lower':>6} {'rate':>7} {'dubbed':>7} {'seen':>6}  {'lift':>5}  studio")
    for lower, rate, hits, support, name in rows[:40]:
        lift = rate / base if base else 0.0
        print(
            f"   {lower * 100:>5.0f}% {rate * 100:>6.0f}% {hits:>7} {support:>6}"
            f"  {lift:>4.1f}x  {name[:40]}",
            flush=True,
        )

    strong = [r for r in rows if r[0] >= 0.5]
    print()
    print("=" * 72)
    print("candidates: lower bound >= 50%")
    print("=" * 72)
    if strong:
        for lower, rate, hits, support, name in strong:
            lift = rate / base if base else 0.0
            print(
                f"   {name[:40]:<40} {hits}/{support} dubbed,"
                f" bound {lower * 100:.0f}%, {lift:.1f}x base",
                flush=True,
            )
        print(
            "\n   These are the names to encode, with their measured bounds -"
            "\n   not a hand-written list of companies I believe dub things.",
            flush=True,
        )
    else:
        print(
            "   None reach it at this sample size. Either the signal is weaker"
            "\n   than the first probe's 8.4x suggested, or support is too thin;"
            "\n   rerun with a larger --sample before encoding anything.",
            flush=True,
        )


if __name__ == "__main__":
    main()
