"""Can a cast be fetched for a PAGE of media, or only one title at a time?

This decides the cost of the English dub walk by a factor of fifty. Per
title it is 22,167 requests against a two-second floor, which is 12.3
hours and so cannot run in CI. Fifty titles per request is 444 requests,
about fifteen minutes.

The walk went per-title because an early paged probe returned zero
English voice actors for every title, Cowboy Bebop and Death Note
included. That probe also omitted `node { id }`, and `voiceActors`
resolves to null without it - silently, with no error. Two variables
changed at once when it was fixed, and the per-title shape got the
credit. This separates them:

    paged_with_node      the shape that was never actually tried
    paged_without_node   reproduces the original failure
    single_with_node      the control that is known to work

If paged_with_node matches single_with_node, paging was never the
problem. Equality is checked per title, not on the total, because a
subset populating would still average out misleadingly.
"""

import argparse
import json
import time

import httpx

URL = "https://graphql.anilist.co"

# Titles with large, well documented English casts, plus two known
# Japanese-only entries. A zero on the first group is a failure of the
# query, not a fact about the title - that is what makes them usable.
KNOWN = {
    1: ("Cowboy Bebop", True),
    1535: ("Death Note", True),
    113415: ("Jujutsu Kaisen", True),
    10087: ("Fate/Zero", True),
    20954: ("Koe no Katachi", True),
    21: ("One Piece", True),
    97938: ("Kemono Friends", False),
    127549: ("Kageki Shoujo", False),
}

CAST_FIELDS_WITH_NODE = """
        edges {
          node { id }
          voiceActors(language: ENGLISH) { id }
        }
"""

CAST_FIELDS_NO_NODE = """
        edges {
          voiceActors(language: ENGLISH) { id }
        }
"""

PAGED = """
query ($ids: [Int], $perPage: Int, $chars: Int) {
  Page(page: 1, perPage: $perPage) {
    media(id_in: $ids, type: ANIME) {
      id
      characters(perPage: $chars, sort: ROLE) {
CAST_FIELDS
      }
    }
  }
}
"""

SINGLE = """
query ($id: Int, $chars: Int) {
  Media(id: $id, type: ANIME) {
    id
    characters(perPage: $chars, sort: ROLE) {
CAST_FIELDS
    }
  }
}
"""


def post(client: httpx.Client, query: str, variables: dict) -> tuple[dict, dict]:
    for attempt in range(5):
        resp = client.post(URL, json={"query": query, "variables": variables})
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After") or 2**attempt)
            print(f"  429, sleeping {wait}s", flush=True)
            time.sleep(wait)
            continue
        return resp.json(), dict(resp.headers)
    raise SystemExit("rate limited out")


def count_edges(media: dict) -> tuple[int, int]:
    """Returns (edges seen, English VA credits)."""
    edges = (media.get("characters") or {}).get("edges") or []
    vas = sum(len(e.get("voiceActors") or []) for e in edges)
    return len(edges), vas


def run_paged(client, ids, chars, with_node, per_page):
    query = PAGED.replace(
        "CAST_FIELDS", CAST_FIELDS_WITH_NODE if with_node else CAST_FIELDS_NO_NODE
    )
    payload, headers = post(client, query, {"ids": ids, "perPage": per_page, "chars": chars})
    if payload.get("errors"):
        print("  errors:", json.dumps(payload["errors"])[:400], flush=True)
    page = (payload.get("data") or {}).get("Page") or {}
    out = {}
    for media in page.get("media") or []:
        out[media["id"]] = count_edges(media)
    return out, headers


def run_single(client, ids, chars, with_node, min_interval):
    query = SINGLE.replace(
        "CAST_FIELDS", CAST_FIELDS_WITH_NODE if with_node else CAST_FIELDS_NO_NODE
    )
    out = {}
    headers = {}
    for media_id in ids:
        payload, headers = post(client, query, {"id": media_id, "chars": chars})
        if payload.get("errors"):
            print("  errors:", json.dumps(payload["errors"])[:300], flush=True)
        media = (payload.get("data") or {}).get("Media")
        if media:
            out[media["id"]] = count_edges(media)
        time.sleep(min_interval)
    return out, headers


def report(label: str, got: dict, headers: dict) -> None:
    print(f"\n[{label}]", flush=True)
    print(f"  {'id':>7} {'edges':>6} {'en_va':>6}  title", flush=True)
    for media_id, (name, _expected) in KNOWN.items():
        edges, vas = got.get(media_id, (None, None))
        shown = "absent" if edges is None else f"{edges:>6} {vas:>6}"
        print(f"  {media_id:>7} {shown}  {name}", flush=True)
    live = sum(v for _e, v in got.values())
    print(f"  total English VA credits: {live}", flush=True)
    print(f"  rate limit remaining: {headers.get('X-RateLimit-Remaining')}", flush=True)


IDS_QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    media(type: ANIME, sort: POPULARITY_DESC) { id title { romaji } }
  }
}
"""


def probe_real_batch(client, count: int, chars: int, min_interval: float) -> None:
    """A real batch is 50 ids x 25 characters, which the 8-id test never sized.

    AniList enforces a query complexity limit, so the chunk size the walk can
    actually use is an empirical question, not simply 50 because perPage allows
    it. A batch silently truncated or served with null casts would reintroduce
    the catalog-wide false zero this probe exists to rule out.
    """
    payload, _ = post(client, IDS_QUERY, {"page": 1, "perPage": count})
    media = ((payload.get("data") or {}).get("Page") or {}).get("media") or []
    ids = [m["id"] for m in media]
    print(f"\nfetched {len(ids)} real anime ids to request in ONE call", flush=True)
    if not ids:
        print("  could not fetch ids; skipping", flush=True)
        return

    time.sleep(min_interval)
    query = PAGED.replace("CAST_FIELDS", CAST_FIELDS_WITH_NODE)
    payload, headers = post(client, query, {"ids": ids, "perPage": len(ids), "chars": chars})
    if payload.get("errors"):
        print("  ERRORS:", json.dumps(payload["errors"])[:600], flush=True)
    returned = ((payload.get("data") or {}).get("Page") or {}).get("media") or []
    print(f"  requested {len(ids)} ids, got {len(returned)} media back", flush=True)

    populated = null_edges = empty_cast = 0
    for m in returned:
        edges = (m.get("characters") or {}).get("edges") or []
        if any(e.get("voiceActors") is None for e in edges):
            null_edges += 1
            continue
        if sum(len(e.get("voiceActors") or []) for e in edges):
            populated += 1
        elif edges:
            empty_cast += 1
    print(
        f"  {populated} have an English cast, {empty_cast} have characters but"
        f" no English cast, {null_edges} returned NULL voiceActors",
        flush=True,
    )
    print(f"  rate limit remaining: {headers.get('X-RateLimit-Remaining')}", flush=True)
    if len(returned) < len(ids):
        print(
            f"  WARNING: truncated by {len(ids) - len(returned)}. The walk must"
            " chunk smaller than this or it will silently skip titles.",
            flush=True,
        )
    elif null_edges:
        print("  WARNING: null voiceActors at this batch size.", flush=True)
    else:
        print(
            f"  OK: a {len(ids)}-id batch is served whole, so the walk costs"
            f" ceil(N/{len(ids)}) requests.",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe paged cast retrieval")
    parser.add_argument("--chars", type=int, default=25)
    parser.add_argument(
        "--batches",
        default="50,25",
        help="real-id batch sizes to request in a single call",
    )
    parser.add_argument("--min-interval", type=float, default=2.0)
    parser.add_argument(
        "--sizes",
        default="50,25,10",
        help="comma-separated Page perPage sizes to try for the paged shape",
    )
    args = parser.parse_args()
    ids = list(KNOWN)

    with httpx.Client(timeout=90.0, headers={"Content-Type": "application/json"}) as client:
        print("=" * 72)
        print("control: one request per title, node selected (the current walk)")
        print("=" * 72)
        control, headers = run_single(client, ids, args.chars, True, args.min_interval)
        report("single_with_node", control, headers)

        print()
        print("=" * 72)
        print("the shape that was never tried: a page of media, node selected")
        print("=" * 72)
        for per_page in [int(s) for s in args.sizes.split(",") if s.strip()]:
            time.sleep(args.min_interval)
            paged, headers = run_paged(client, ids, args.chars, True, per_page)
            report(f"paged_with_node perPage={per_page}", paged, headers)
            # Per title, not on the total: a subset populating would still
            # produce a plausible-looking sum.
            agree = [i for i in ids if paged.get(i, (0, 0))[1] == control.get(i, (0, 0))[1]]
            missing = [i for i in ids if paged.get(i, (0, 0))[1] < control.get(i, (0, 0))[1]]
            print(
                f"  matches control on {len(agree)}/{len(ids)} titles; short on {len(missing)}",
                flush=True,
            )
            if len(agree) == len(ids):
                print(
                    "  VERDICT: paging works. The per-title walk is unnecessary"
                    f" and the pass costs ceil(N/{per_page}) requests, not N.",
                    flush=True,
                )

        print()
        print("=" * 72)
        print("reproduce the original failure: a page of media, node omitted")
        print("=" * 72)
        time.sleep(args.min_interval)
        no_node, headers = run_paged(client, ids, args.chars, False, 50)
        report("paged_without_node", no_node, headers)

        print()
        print("=" * 72)
        print("and single without node, to place the blame precisely")
        print("=" * 72)
        single_no_node, headers = run_single(client, ids[:3], args.chars, False, args.min_interval)
        report("single_without_node (first 3)", single_no_node, headers)

        print()
        print("=" * 72)
        print("how many real ids can one call actually carry?")
        print("=" * 72)
        for count in [int(b) for b in args.batches.split(",") if b.strip()]:
            time.sleep(args.min_interval)
            probe_real_batch(client, count, args.chars, args.min_interval)


if __name__ == "__main__":
    main()
