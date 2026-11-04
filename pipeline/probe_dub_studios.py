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

OUTCOME: measured, and not worth building. Recorded here so the question is
not reopened from the encouraging half of the numbers.

The precision is real - Funimation reaches 92% on 36 titles with a 78%
lower bound against a 34.8% base rate, and the ceiling is 2.9x - but
precision and reach are inversely related, which kills it. A licensor whose
titles reliably get their casts entered has nothing left to fix: Funimation
would correct 3 titles in 998, Netflix and Central Park Media zero. The
licensors with titles to fix are the ones whose dub claim is weakest -
Sentai Filmworks 9 fixes at 64%, Crunchyroll 5 at 58%, Discotek 3 at 40%.

So the choice is between a precise signal that reaches almost nothing and a
broad one that is frequently wrong:

    precision >= 0.75   ~182 titles (0.8% of catalog), ~28 of them wrong
    all licensors       ~681 titles (3.0% of catalog), ~225 of them wrong

Against 7,016 titles already covered, the careful option adds 2.6% while
asserting ~28 dubs that do not exist, and the broad option asserts ~225. A
project that refuses to serve english_va_count = 0 as "sub only" because a
zero is unreliable should not assert a dub on a distributor credit, and
franchise propagation already added 2,537 titles for free on a mechanism
that is actually tight - same production, same language.

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
import random
from collections import Counter

from pipeline.client import RateLimitedClient
from pipeline.stats import wilson_lower

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


def post(client: RateLimitedClient, query: str, variables: dict) -> dict:
    """Through the project's client, not a bare httpx call.

    A bare client.post killed a 1,000-title run on its 40th request with
    "Server disconnected without sending a response" - a transient
    RemoteProtocolError, which subclasses httpx.TransportError and which
    RateLimitedClient already retries with backoff. It also paces requests
    and caches responses on disk, so a rerun over the same sampled ids costs
    almost nothing. Hand-rolling retries here would have been the fifth copy
    of that logic in these probes.
    """
    payload = client.request("POST", URL, json_body={"query": query, "variables": variables})
    if payload.get("errors"):
        print(f"   errors: {json.dumps(payload['errors'])[:240]}", flush=True)
    return payload


def sample_ids(client: RateLimitedClient, want: int, seed: int) -> list[int]:
    """Random across the whole id space; a popularity sample cannot reach the
    tail past AniList's 5000-row offset cap, and the tail is the subject."""
    rng = random.Random(seed)
    out: list[int] = []
    while len(out) < want:
        payload = post(client, IDS_QUERY, {"ids": [rng.randint(1, 190_000) for _ in range(50)]})
        media = ((payload.get("data") or {}).get("Page") or {}).get("media") or []
        out.extend(m["id"] for m in media)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Derive which studios predict a dub")
    parser.add_argument("--sample", type=int, default=1000)
    parser.add_argument("--min-support", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261103)
    parser.add_argument(
        "--cache-dir",
        default="",
        help="on-disk response cache, so a rerun over the same ids is free",
    )
    args = parser.parse_args()

    client = RateLimitedClient(min_interval=2.0, cache_dir=args.cache_dir, timeout=120.0)
    ids = sample_ids(client, args.sample, args.seed)
    print(f"sampled {len(ids)} random anime ids\n", flush=True)
    records = []
    for start in range(0, len(ids), 50):
        chunk = ids[start : start + 50]
        payload = post(client, QUERY, {"ids": chunk, "perPage": len(chunk)})
        page = (payload.get("data") or {}).get("Page") or {}
        records.extend(page.get("media") or [])

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

    # Companies whose BUSINESS is English-language licensing or dubbing.
    #
    # This list is mechanistic on purpose, and it is the one part of this
    # probe that is not derived from the data - because the data is
    # confounded. Yomiko Advertising scored 100%, Hakuhodo 80%, Lantis 65%,
    # AT-X 57%, KADOKAWA 58%, Pony Canyon 45%. None of them dub anything.
    # They attach to commercially significant titles, and commercially
    # significant titles get dubbed, so they are a popularity proxy wearing a
    # studio credit.
    #
    # Inferring a dub from them would put popularity into the dub filter,
    # which the project forbids in scoring for exactly this reason: it would
    # systematically favour well-known titles and bury the obscure ones the
    # recommender exists to surface. So a studio qualifies on mechanism
    # first - it must plausibly cause an English dub - and on measured
    # precision second.
    ENGLISH_LICENSORS = (
        "funimation",
        "sentai filmworks",
        "viz media",
        "central park media",
        "the right stuf",
        "media blasters",
        "adv films",
        "bandai entertainment",
        "aniplex of america",
        "discotek",
        "nis america",
        "geneon",
        "pioneer entertainment",
        "manga entertainment",
        "crunchyroll",
        "netflix",
        "bang zoom",
        "studiopolis",
        "nyav post",
        "ocean productions",
        "ocean group",
        "seraphim digital",
        "new generation pictures",
        "sound cadence",
    )

    def is_licensor(name: str) -> bool:
        low = name.lower()
        return any(hint in low for hint in ENGLISH_LICENSORS)

    print()
    print("=" * 72)
    print("the confound: who scores well WITHOUT dubbing anything")
    print("=" * 72)
    confounded = [r for r in rows if r[0] >= 0.3 and not is_licensor(r[4])]
    for lower, rate, _hits, support, name in confounded[:14]:
        print(
            f"   {name[:40]:<40} {rate * 100:>3.0f}% of {support:<3}  bound {lower * 100:>3.0f}%",
            flush=True,
        )
    print(
        "\n   These are ad agencies, music labels and TV networks. They predict"
        "\n   a dub by predicting commercial significance, which is popularity -"
        "\n   excluded from scoring by project rule, and self-defeating in a"
        "\n   recommender built to surface obscure titles.",
        flush=True,
    )

    print()
    print("=" * 72)
    print("actionable: English licensors, and how many titles they would FIX")
    print("=" * 72)
    # The inference target is a title crediting a licensor while having a
    # cast recorded but no English cast - an AniList false negative.
    targets: Counter = Counter()
    for media in with_cast:
        if has_english(media):
            continue
        for name in set(studios(media)):
            if is_licensor(name):
                targets[name] += 1
    print(f"   {'studio':<40} {'precision':>10} {'support':>8} {'would fix':>10}")
    for _lower, rate, _hits, support, name in rows:
        if not is_licensor(name):
            continue
        fix = targets.get(name, 0)
        print(
            f"   {name[:40]:<40} {rate * 100:>9.0f}% {support:>8} {fix:>10}",
            flush=True,
        )
    seen_titles = set()
    for media in with_cast:
        if has_english(media):
            continue
        if any(is_licensor(n) for n in studios(media)):
            seen_titles.add(media["id"])
    total_targets = len(seen_titles)
    sampled = len(records) or 1
    catalog = 22667
    print(
        f"\n   distinct titles a licensor credit would mark as dubbed:"
        f" {total_targets}/{sampled}"
        f"\n   extrapolated to the catalog: ~{round(catalog * total_targets / sampled)} titles"
        f" ({100 * total_targets / sampled:.1f}%)",
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
