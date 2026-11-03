"""Ask Kitsu what its cast API actually is, instead of guessing at it.

Four of six endpoint and filter guesses in the first Kitsu probe returned
HTTP 400, and the probe discarded the response bodies - so it learned
nothing from four failures that were very likely explaining themselves.
JSON:API error documents normally name the offending parameter.

Two specific suspicions to settle, both of which change the answer:

  1. `castings` may be the legacy model. Its `language` is free text and
     returned "Brazilian" where every other source says Portuguese, while
     `character-voices` carries a `locale` and holds 207,844 rows. If the
     current path is anime -> media-characters -> character-voices, then the
     earlier measurement was taken against a deprecated resource with dirty
     values, and clean locale codes were available the whole time.

  2. Kitsu's filters may be snake_case. filter[mediaId] works on castings
     but 400'd on media-characters, which is the kind of inconsistency that
     usually means one spelling is the real one.

Documentation could not settle either: kitsu.docs.apiary.io and
hummingbird-me.github.io do not resolve from here, and the OpenAPI
conversion in hummingbird-me/api-docs does not yet contain the cast
schemas. So this asks the server and prints what it says.
"""

import argparse
import json

import httpx

BASE = "https://kitsu.io/api/edge"
HEADERS = {
    "Accept": "application/vnd.api+json",
    "User-Agent": "MangaBrain/0.1 (self-hosted recommendation engine; dub coverage probe)",
}


def probe(client: httpx.Client, label: str, path: str, params: dict | None = None) -> dict | None:
    """Try one call and report what came back, errors included verbatim."""
    url = f"{BASE}{path}"
    try:
        resp = client.get(url, params=params, headers=HEADERS)
    except httpx.HTTPError as exc:
        print(f"  {label:<40} {type(exc).__name__}", flush=True)
        return None
    if resp.status_code != 200:
        # The whole point: a 400 usually says which parameter it disliked.
        detail = resp.text[:400].replace("\n", " ")
        print(f"  {label:<40} HTTP {resp.status_code}  {detail}", flush=True)
        return None
    try:
        body = resp.json()
    except ValueError:
        print(f"  {label:<40} 200 but not JSON", flush=True)
        return None
    rows = body.get("data")
    count = (body.get("meta") or {}).get("count")
    n = len(rows) if isinstance(rows, list) else (1 if rows else 0)
    print(f"  {label:<40} OK  rows={n} meta.count={count}", flush=True)
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover Kitsu's cast API shape")
    parser.add_argument("--anime", default="1", help="Kitsu anime id to inspect")
    args = parser.parse_args()
    anime = args.anime

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        print("=" * 72)
        print("1. filter spellings: camelCase vs snake_case, and comma lists")
        print("=" * 72)
        for label, path, params in [
            ("castings filter[mediaId]", "/castings", {"filter[mediaId]": anime}),
            ("castings filter[media_id]", "/castings", {"filter[media_id]": anime}),
            ("castings filter[mediaId] comma", "/castings", {"filter[mediaId]": "1,2,3"}),
            ("castings filter[media_id] comma", "/castings", {"filter[media_id]": "1,2,3"}),
            ("castings filter[voiceActor]", "/castings", {"filter[voiceActor]": "true"}),
            ("castings filter[voice_actor]", "/castings", {"filter[voice_actor]": "true"}),
            ("castings filter[isVoiceActor]", "/castings", {"filter[isVoiceActor]": "true"}),
            ("castings filter[language]", "/castings", {"filter[language]": "English"}),
            ("media-characters filter[mediaId]", "/media-characters", {"filter[mediaId]": anime}),
            (
                "media-characters filter[media_id]",
                "/media-characters",
                {"filter[media_id]": anime},
            ),
        ]:
            probe(client, label, path, params)

        print()
        print("=" * 72)
        print("2. the join path: anime -> characters -> voices")
        print("=" * 72)
        body = probe(
            client,
            "anime/{id}/characters include=voices",
            f"/anime/{anime}/characters",
            {"include": "voices", "page[limit]": 5},
        )
        if body:
            included = body.get("included") or []
            kinds: dict[str, int] = {}
            for item in included:
                kinds[item.get("type", "?")] = kinds.get(item.get("type", "?"), 0) + 1
            print(f"    included types: {kinds}", flush=True)
            voice = next((i for i in included if i.get("type") == "characterVoices"), None)
            if voice is None:
                voice = next(
                    (i for i in included if "voice" in (i.get("type") or "").lower()), None
                )
            if voice:
                print(f"    voice attributes: {list(voice.get('attributes') or {})}", flush=True)
                print(f"    voice sample: {json.dumps(voice.get('attributes'))[:200]}", flush=True)
            else:
                print("    no voice objects in `included`", flush=True)

        probe(
            client,
            "anime/{id}/characters include=voices.person",
            f"/anime/{anime}/characters",
            {"include": "voices.person", "page[limit]": 5},
        )

        print()
        print("=" * 72)
        print("3. character-voices: what does locale look like, and can it filter?")
        print("=" * 72)
        body = probe(
            client, "character-voices (unfiltered)", "/character-voices", {"page[limit]": 20}
        )
        if body:
            locales: dict[str, int] = {}
            for row in body.get("data") or []:
                loc = (row.get("attributes") or {}).get("locale") or "(none)"
                locales[loc] = locales.get(loc, 0) + 1
            print(f"    locale values in 20 rows: {locales}", flush=True)
        for label, params in [
            ("character-voices filter[locale]", {"filter[locale]": "en", "page[limit]": 5}),
            (
                "character-voices filter[mediaCharacterId]",
                {"filter[mediaCharacterId]": "1", "page[limit]": 5},
            ),
            (
                "character-voices filter[media_character_id]",
                {"filter[media_character_id]": "1", "page[limit]": 5},
            ),
        ]:
            probe(client, label, "/character-voices", params)

        print()
        print("=" * 72)
        print("4. how large can a page be?")
        print("=" * 72)
        for limit in (20, 40, 100, 250):
            probe(
                client,
                f"castings page[limit]={limit}",
                "/castings",
                {"filter[mediaId]": anime, "page[limit]": limit},
            )

        print()
        print("=" * 72)
        print("5. mappings: exact externalSite values and comma lists")
        print("=" * 72)
        body = probe(client, "mappings (unfiltered sample)", "/mappings", {"page[limit]": 20})
        if body:
            sites: dict[str, int] = {}
            for row in body.get("data") or []:
                site = (row.get("attributes") or {}).get("externalSite") or "?"
                sites[site] = sites.get(site, 0) + 1
            print(f"    externalSite values: {sites}", flush=True)
        for label, params in [
            (
                "mappings anilist/anime",
                {"filter[externalSite]": "anilist/anime", "filter[externalId]": "1"},
            ),
            (
                "mappings myanimelist/anime",
                {"filter[externalSite]": "myanimelist/anime", "filter[externalId]": "1"},
            ),
            (
                "mappings comma externalId",
                {"filter[externalSite]": "anilist/anime", "filter[externalId]": "1,1535,113415"},
            ),
            (
                "mappings comma + limit 100",
                {
                    "filter[externalSite]": "anilist/anime",
                    "filter[externalId]": "1,1535,113415",
                    "page[limit]": 100,
                },
            ),
        ]:
            probe(client, label, "/mappings", params)


if __name__ == "__main__":
    main()
