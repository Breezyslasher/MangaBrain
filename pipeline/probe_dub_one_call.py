"""Can one request return every dub language, by not filtering at all?

The language walk costs ~444 requests per language because each pass asks
voiceActors(language: X). Nine languages is ~4,000 requests and most of a
working day. Aliasing nine filtered fields into one request was tried and
silently returns the same list under every alias.

But there is a third shape that was never tried: drop the filter entirely.
`voiceActors` without a language argument should return every voice actor
for the character, and AniList's Staff type carries `languageV2`, which is
almost certainly the field the language: filter matches on. If so, one
unfiltered request yields all ten languages at once - ~444 requests for the
whole set rather than per language, a tenfold saving - and the per-language
walk this would replace was built on an assumption nobody checked.

The check has to be per-language equality against the filtered queries,
not a plausible-looking total. Two shapes agreeing on a sum while
disagreeing per language is exactly how the aliased attempt looked
convincing: identical counts for all nine languages, including a Hebrew dub
with as many voice actors as the English one.
"""

import argparse
import json
import time
from collections import Counter

import httpx

URL = "https://graphql.anilist.co"

KNOWN = {
    1: "Cowboy Bebop",
    1535: "Death Note",
    113415: "Jujutsu Kaisen",
    21: "One Piece",
    20954: "Koe no Katachi",
}

LANGUAGES = (
    "JAPANESE",
    "ENGLISH",
    "KOREAN",
    "ITALIAN",
    "SPANISH",
    "PORTUGUESE",
    "FRENCH",
    "GERMAN",
    "HEBREW",
    "HUNGARIAN",
)

# No language argument, and languageV2 read off each voice actor instead.
UNFILTERED = """
query ($ids: [Int], $perPage: Int, $chars: Int) {
  Page(page: 1, perPage: $perPage) {
    media(id_in: $ids, type: ANIME) {
      id
      characters(perPage: $chars, sort: ROLE) {
        edges {
          node { id }
          voiceActors { id languageV2 }
        }
      }
    }
  }
}
"""

# The control: the shape the walk uses now, one language per request.
FILTERED = """
query ($ids: [Int], $perPage: Int, $chars: Int) {
  Page(page: 1, perPage: $perPage) {
    media(id_in: $ids, type: ANIME) {
      id
      characters(perPage: $chars, sort: ROLE) {
        edges {
          node { id }
          voiceActors(language: LANG) { id }
        }
      }
    }
  }
}
"""


def post(client: httpx.Client, query: str, variables: dict) -> dict:
    resp = client.post(URL, json={"query": query, "variables": variables})
    payload = resp.json()
    if payload.get("errors"):
        print(f"   errors: {json.dumps(payload['errors'])[:300]}", flush=True)
    return payload


def actors_by_language(media: dict) -> dict[str, set[int]]:
    """Distinct voice actors per languageV2, from the unfiltered shape."""
    out: dict[str, set[int]] = {}
    for edge in (media.get("characters") or {}).get("edges") or []:
        for actor in edge.get("voiceActors") or []:
            lang = (actor.get("languageV2") or "?").upper()
            out.setdefault(lang, set()).add(actor["id"])
    return out


def distinct_actors(media: dict) -> set[int]:
    out: set[int] = set()
    for edge in (media.get("characters") or {}).get("edges") or []:
        for actor in edge.get("voiceActors") or []:
            out.add(actor["id"])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="One call for every dub language?")
    parser.add_argument("--chars", type=int, default=25)
    parser.add_argument("--min-interval", type=float, default=2.0)
    args = parser.parse_args()
    ids = list(KNOWN)

    with httpx.Client(timeout=120.0, headers={"Content-Type": "application/json"}) as client:
        print("=" * 72)
        print("1. one unfiltered request, languages read from languageV2")
        print("=" * 72)
        payload = post(client, UNFILTERED, {"ids": ids, "perPage": len(ids), "chars": args.chars})
        media_list = ((payload.get("data") or {}).get("Page") or {}).get("media") or []
        if not media_list:
            raise SystemExit("the unfiltered shape returned nothing")
        unfiltered: dict[int, dict[str, set[int]]] = {}
        for media in media_list:
            counts = actors_by_language(media)
            unfiltered[media["id"]] = counts
            shown = ", ".join(f"{k.lower()}={len(v)}" for k, v in sorted(counts.items()))
            print(f"   {KNOWN.get(media['id'], '?')[:26]:<26} {shown or 'none'}", flush=True)
        seen_langs = sorted({lang for c in unfiltered.values() for lang in c})
        print(f"\n   languages seen in ONE request: {len(seen_langs)} -> {seen_langs}", flush=True)

        print()
        print("=" * 72)
        print("2. the control: one filtered request per language")
        print("=" * 72)
        control: dict[str, dict[int, int]] = {}
        for language in LANGUAGES:
            time.sleep(args.min_interval)
            payload = post(
                client,
                FILTERED.replace("LANG", language),
                {"ids": ids, "perPage": len(ids), "chars": args.chars},
            )
            per_title = {
                m["id"]: len(distinct_actors(m))
                for m in ((payload.get("data") or {}).get("Page") or {}).get("media") or []
            }
            control[language] = per_title
            total = sum(per_title.values())
            print(f"   {language:<12} total={total}", flush=True)

        print()
        print("=" * 72)
        print("3. do they agree PER LANGUAGE and PER TITLE?")
        print("=" * 72)
        mismatches: list[str] = []
        for language in LANGUAGES:
            for media_id in ids:
                want = control[language].get(media_id, 0)
                got = len(unfiltered.get(media_id, {}).get(language, set()))
                if want != got:
                    name = KNOWN[media_id][:20]
                    mismatches.append(
                        f"{language.lower()}/{name}: filtered={want} unfiltered={got}"
                    )
        if mismatches:
            print(f"   {len(mismatches)} disagreement(s):", flush=True)
            for line in mismatches[:25]:
                print(f"     {line}", flush=True)
            print(
                "\n   The unfiltered shape does NOT reproduce the filtered one."
                "\n   Do not collapse the per-language passes.",
                flush=True,
            )
        else:
            saving = len(LANGUAGES)
            print("   Exact agreement on every language and every title.", flush=True)
            print(
                f"\n   So one unfiltered request replaces {saving} filtered ones:"
                f"\n   ~444 requests for the whole catalog instead of ~{444 * saving},"
                "\n   and the language set is not limited to the enum - languageV2"
                "\n   is whatever AniList recorded for the voice actor.",
                flush=True,
            )
        counter = Counter(lang for counts in unfiltered.values() for lang in counts)
        print(f"\n   titles per language in the one request: {dict(counter)}", flush=True)


if __name__ == "__main__":
    main()
