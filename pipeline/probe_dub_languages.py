"""Can AniList answer for every dub language, not just English?

The walk asks `voiceActors(language: ENGLISH)` and nothing else, so every
non-English dub in the database comes from ANN - and ANN is capped by name
matching at 31.8% of the catalog. AniList needs no matching: it answers for
the exact id we asked about, over all 22,667 titles.

GraphQL aliases make that free in requests. One `voiceActors` field per
language in the same selection returns them all in the same response, so the
paged walk stays ~444 requests and simply comes back with more per title.

Three things decide whether this works, and none of them should be guessed:

  1. Which languages the StaffLanguage enum actually has. Introspected here
     rather than recalled, because the answer sets the ceiling.
  2. Whether the aliased query survives AniList's complexity limit at a
     realistic page size. Fifty titles times twenty-five characters times N
     languages is a large query, and the limit is enforced.
  3. Whether the counts agree with ANN where both have an opinion, which is
     the only check that the field means what it looks like it means.
"""

import argparse
import json
import time

import httpx

URL = "https://graphql.anilist.co"

# Titles with documented dubs in several languages, so a zero is a failure of
# the query rather than a fact about the title.
KNOWN = {
    1: "Cowboy Bebop",
    1535: "Death Note",
    113415: "Jujutsu Kaisen",
    21: "One Piece",
    20954: "Koe no Katachi",
}

ENUM_QUERY = """
query {
  __type(name: "StaffLanguage") { enumValues { name } }
}
"""

# The shape already in production and known correct. Any multi-language shape
# has to reproduce its English numbers or it is returning something else.
CONTROL_QUERY = """
query ($ids: [Int], $perPage: Int, $chars: Int) {
  Page(page: 1, perPage: $perPage) {
    media(id_in: $ids, type: ANIME) {
      id
      characters(perPage: $chars, sort: ROLE) {
        edges {
          node { id }
          voiceActors(language: ENGLISH) { id }
        }
      }
    }
  }
}
"""


def post(client: httpx.Client, query: str, variables: dict | None = None):
    resp = client.post(URL, json={"query": query, "variables": variables or {}})
    return resp.json(), dict(resp.headers)


def build_cast_query(languages: list[str], chars: int) -> str:
    """One aliased voiceActors field per language, in a single selection."""
    fields = "\n".join(
        f"          {lang.lower()}: voiceActors(language: {lang}) {{ id }}" for lang in languages
    )
    return f"""
query ($ids: [Int], $perPage: Int) {{
  Page(page: 1, perPage: $perPage) {{
    media(id_in: $ids, type: ANIME) {{
      id
      countryOfOrigin
      characters(perPage: {chars}, sort: ROLE) {{
        edges {{
          node {{ id }}
{fields}
        }}
      }}
    }}
  }}
}}
"""


def build_single_language_query(language: str, chars: int) -> str:
    """One language per request: 9x the requests, but it is the shape that
    production already proves works, just with the enum value swapped."""
    return f"""
query ($ids: [Int], $perPage: Int) {{
  Page(page: 1, perPage: $perPage) {{
    media(id_in: $ids, type: ANIME) {{
      id
      characters(perPage: {chars}, sort: ROLE) {{
        edges {{
          node {{ id }}
          voiceActors(language: {language}) {{ id }}
        }}
      }}
    }}
  }}
}}
"""


def count_actors(media: dict, key: str = "voiceActors") -> int:
    actors: set[int] = set()
    for edge in (media.get("characters") or {}).get("edges") or []:
        for actor in edge.get(key) or []:
            actors.add(actor["id"])
    return len(actors)


def count_by_language(media: dict, languages: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for lang in languages:
        key = lang.lower()
        actors: set[int] = set()
        for edge in (media.get("characters") or {}).get("edges") or []:
            for actor in edge.get(key) or []:
                actors.add(actor["id"])
        if actors:
            out[key] = len(actors)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe AniList's dub languages")
    parser.add_argument("--chars", type=int, default=25)
    parser.add_argument("--min-interval", type=float, default=2.0)
    parser.add_argument(
        "--page-sizes",
        default="50,25,10",
        help="page sizes to try with every language aliased at once",
    )
    args = parser.parse_args()

    with httpx.Client(timeout=120.0, headers={"Content-Type": "application/json"}) as client:
        print("=" * 72)
        print("1. which languages does StaffLanguage actually offer?")
        print("=" * 72)
        payload, _ = post(client, ENUM_QUERY)
        if payload.get("errors"):
            print("errors:", json.dumps(payload["errors"])[:500], flush=True)
        values = ((payload.get("data") or {}).get("__type") or {}).get("enumValues") or []
        languages = [v["name"] for v in values]
        print(f"   {len(languages)} values: {', '.join(languages)}", flush=True)
        if not languages:
            raise SystemExit("could not introspect the enum")

        # Japanese is the original for almost the whole catalog, not a dub.
        dub_languages = [lang for lang in languages if lang != "JAPANESE"]
        print(f"   {len(dub_languages)} of those are candidate DUB languages", flush=True)

        print()
        print("=" * 72)
        print("2. does the fully aliased query survive the complexity limit?")
        print("=" * 72)
        ids = list(KNOWN)
        query = build_cast_query(dub_languages, args.chars)
        for per_page in [int(p) for p in args.page_sizes.split(",") if p.strip()]:
            time.sleep(args.min_interval)
            payload, headers = post(client, query, {"ids": ids, "perPage": per_page})
            if payload.get("errors"):
                print(
                    f"   perPage={per_page:<3} REJECTED: {json.dumps(payload['errors'])[:300]}",
                    flush=True,
                )
                continue
            media = ((payload.get("data") or {}).get("Page") or {}).get("media") or []
            print(
                f"   perPage={per_page:<3} OK, {len(media)}/{len(ids)} media returned"
                f", rate limit remaining {headers.get('X-RateLimit-Remaining')}",
                flush=True,
            )

        print()
        print("=" * 72)
        print("3. control: the single-language shape production already uses")
        print("=" * 72)
        time.sleep(args.min_interval)
        payload, _ = post(
            client, CONTROL_QUERY, {"ids": ids, "perPage": len(ids), "chars": args.chars}
        )
        control = {
            m["id"]: count_actors(m)
            for m in ((payload.get("data") or {}).get("Page") or {}).get("media") or []
        }
        for media_id, name in KNOWN.items():
            print(f"   {name[:28]:<28} english={control.get(media_id, 0)}", flush=True)

        print()
        print("=" * 72)
        print("4. the aliased shape, checked against that control")
        print("=" * 72)
        time.sleep(args.min_interval)
        payload, _ = post(client, query, {"ids": ids, "perPage": len(ids)})
        if payload.get("errors"):
            print("   errors:", json.dumps(payload["errors"])[:400], flush=True)
        aliased: dict[int, dict[str, int]] = {}
        for media in ((payload.get("data") or {}).get("Page") or {}).get("media") or []:
            counts = count_by_language(media, dub_languages)
            aliased[media["id"]] = counts
            name = KNOWN.get(media["id"], str(media["id"]))
            shown = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"
            print(f"   {name[:28]:<28} {shown}", flush=True)

        # Two failure signatures, both of which return a plausible shape and
        # no error, which is exactly how the missing-node bug survived.
        disagrees = [
            (KNOWN[i], control.get(i, 0), aliased.get(i, {}).get("english", 0))
            for i in ids
            if control.get(i, 0) != aliased.get(i, {}).get("english", 0)
        ]
        flat = [
            KNOWN[i]
            for i, counts in aliased.items()
            if len(counts) > 1 and len(set(counts.values())) == 1
        ]
        print()
        if disagrees:
            print("   FAIL: english disagrees with the control:", flush=True)
            for name, want, got in disagrees:
                print(f"     {name[:28]:<28} control={want} aliased={got}", flush=True)
        if flat:
            print(
                "   FAIL: every language returned an identical count for"
                f" {', '.join(n[:24] for n in flat)}."
                "\n     The language argument is being ignored on the aliased"
                " fields; the same\n     list is served under every alias.",
                flush=True,
            )
        if not disagrees and not flat:
            print(
                "   PASS: english matches the control and the languages differ from each other.",
                flush=True,
            )

        print()
        print("=" * 72)
        print("5. fallback: one request per language, the proven shape")
        print("=" * 72)
        totals: dict[str, int] = {}
        for language in dub_languages:
            time.sleep(args.min_interval)
            payload, headers = post(
                client,
                build_single_language_query(language, args.chars),
                {"ids": ids, "perPage": len(ids)},
            )
            if payload.get("errors"):
                print(
                    f"   {language:<12} errors: {json.dumps(payload['errors'])[:160]}", flush=True
                )
                continue
            per_title = {
                m["id"]: count_actors(m)
                for m in ((payload.get("data") or {}).get("Page") or {}).get("media") or []
            }
            totals[language] = sum(per_title.values())
            shown = ", ".join(
                f"{KNOWN[i][:18]}={per_title.get(i, 0)}" for i in ids if per_title.get(i, 0)
            )
            print(f"   {language:<12} total={totals[language]:<5} {shown or '-'}", flush=True)

        print()
        print("=" * 72)
        print("verdict")
        print("=" * 72)
        english_ok = totals.get("ENGLISH", 0) == sum(control.values())
        distinct = len(set(totals.values())) > 1
        if english_ok and distinct:
            reqs = 444 * len(dub_languages)
            print(
                f"   Per-language passes work: ENGLISH reproduces the control and"
                f"\n   the languages differ. {len(dub_languages)} languages over the whole"
                f"\n   catalog is ~{reqs} requests, measured per entry with no title"
                "\n   matching - so not capped by ANN's 31.8%.",
                flush=True,
            )
        else:
            print(
                "   Per-language passes did NOT validate:"
                f" english_matches_control={english_ok},"
                f" languages_differ={distinct}."
                "\n   Do not build on this until it does.",
                flush=True,
            )


if __name__ == "__main__":
    main()
