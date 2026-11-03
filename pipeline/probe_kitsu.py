"""Is Kitsu a usable dub source, and can it be joined by id?

ANN's ceiling is name matching: 12,882 entries against 22,667 catalog rows,
31.8% resolved, and a wrong match asserts another show's dub. Kitsu is worth
probing for one specific reason - it publishes a `mappings` resource that
carries external ids, including MyAnimeList and AniList. If that holds up,
Kitsu joins on an id we already store and the matching problem disappears
entirely, which is the single biggest limit on non-English dub coverage.

So the questions, in the order that decides whether to build on it:

  1. Is it reachable at all, and under which host? Kitsu moved from kitsu.io
     to kitsu.app and both are quoted in the wild.
  2. Do mappings really expose an AniList or MAL id, per title, in bulk?
  3. Does the cast data carry a LANGUAGE, which is the whole point - a cast
     list without one says nothing about dubs.
  4. How dense is it? A source that answers for a handful of popular titles
     adds nothing over what we already measure.

Nothing here is asserted from memory. Endpoint shapes are tried and the raw
JSON keys are dumped, because Kitsu's cast modelling has changed over time
(castings, media-characters, character-voices) and guessing which exists is
how a probe ends up measuring the wrong thing.
"""

import argparse
import json

import httpx

HOSTS = ["https://kitsu.io/api/edge", "https://kitsu.app/api/edge"]

# AniList ids with large documented multi-language casts, so a thin result is
# a fact about Kitsu rather than about the titles.
KNOWN_ANILIST = {1: "Cowboy Bebop", 1535: "Death Note", 113415: "Jujutsu Kaisen"}

HEADERS = {
    "Accept": "application/vnd.api+json",
    "User-Agent": "MangaBrain/0.1 (self-hosted recommendation engine; dub coverage probe)",
}


def get(client: httpx.Client, url: str, params: dict | None = None):
    try:
        resp = client.get(url, params=params, headers=HEADERS)
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    try:
        return resp.json(), None
    except ValueError:
        return None, "not JSON"


def show_keys(label: str, obj, limit: int = 18) -> None:
    if isinstance(obj, dict):
        keys = list(obj)[:limit]
        print(f"     {label} keys: {keys}", flush=True)
    elif isinstance(obj, list):
        print(f"     {label}: list of {len(obj)}", flush=True)
        if obj:
            show_keys(f"{label}[0]", obj[0], limit)


def probe_reachability(client: httpx.Client) -> str | None:
    print("=" * 72)
    print("1. reachability")
    print("=" * 72)
    for base in HOSTS:
        data, err = get(client, f"{base}/anime", {"page[limit]": 1})
        if err:
            print(f"   {base:<34} {err}", flush=True)
            continue
        count = (data.get("meta") or {}).get("count")
        print(f"   {base:<34} OK, meta.count={count}", flush=True)
        return base
    return None


def probe_mappings(client: httpx.Client, base: str) -> None:
    print()
    print("=" * 72)
    print("2. mappings: can Kitsu be joined on an id we already store?")
    print("=" * 72)
    # Reverse direction first: this is the one that matters. Given an AniList
    # id, can we find the Kitsu entry without matching on a title?
    for anilist_id, name in KNOWN_ANILIST.items():
        data, err = get(
            client,
            f"{base}/mappings",
            {
                "filter[externalSite]": "anilist/anime",
                "filter[externalId]": str(anilist_id),
                "include": "item",
                "page[limit]": 5,
            },
        )
        if err:
            print(f"   anilist/anime {anilist_id} ({name}): {err}", flush=True)
            continue
        rows = data.get("data") or []
        included = data.get("included") or []
        titles = [
            (i.get("attributes") or {}).get("canonicalTitle")
            for i in included
            if i.get("type") == "anime"
        ]
        print(
            f"   anilist/anime {anilist_id:<7} ({name[:18]:<18}) -> {len(rows)} mapping(s)"
            f" item={titles or '-'}",
            flush=True,
        )
        if rows and not titles:
            show_keys("mapping", rows[0])

    # And what sites exist at all, since the filter value must be exact.
    data, err = get(client, f"{base}/mappings", {"page[limit]": 20})
    if not err:
        sites: dict[str, int] = {}
        for row in data.get("data") or []:
            site = (row.get("attributes") or {}).get("externalSite") or "?"
            sites[site] = sites.get(site, 0) + 1
        print(f"   externalSite values in a sample page: {sorted(sites)}", flush=True)


def probe_cast(client: httpx.Client, base: str) -> None:
    print()
    print("=" * 72)
    print("3. does the cast data carry a language?")
    print("=" * 72)
    # Find one Kitsu anime id to work with.
    data, err = get(client, f"{base}/anime", {"filter[text]": "Cowboy Bebop", "page[limit]": 1})
    if err or not (data.get("data") or []):
        print(f"   could not resolve a Kitsu anime id: {err}", flush=True)
        return
    anime = data["data"][0]
    kitsu_id = anime["id"]
    print(
        f"   using Kitsu anime {kitsu_id}"
        f" ({(anime.get('attributes') or {}).get('canonicalTitle')})",
        flush=True,
    )

    # Kitsu has modelled this more than one way over the years, so try each
    # and report which actually answers rather than assuming one.
    shapes = [
        ("castings", f"{base}/castings", {"filter[mediaId]": kitsu_id, "page[limit]": 5}),
        (
            "castings+va",
            f"{base}/castings",
            {
                "filter[mediaId]": kitsu_id,
                "filter[isVoiceActor]": "true",
                "page[limit]": 5,
            },
        ),
        (
            "media-characters",
            f"{base}/media-characters",
            {"filter[mediaId]": kitsu_id, "page[limit]": 5},
        ),
        ("anime/characters", f"{base}/anime/{kitsu_id}/characters", {"page[limit]": 5}),
        ("character-voices", f"{base}/character-voices", {"page[limit]": 5}),
    ]
    for label, url, params in shapes:
        data, err = get(client, url, params)
        if err:
            print(f"   {label:<18} {err}", flush=True)
            continue
        rows = data.get("data") or []
        total = (data.get("meta") or {}).get("count")
        print(f"   {label:<18} OK, {len(rows)} row(s), meta.count={total}", flush=True)
        if rows:
            attrs = rows[0].get("attributes") or {}
            print(f"     attributes: {list(attrs)}", flush=True)
            if "language" in attrs:
                langs: dict[str, int] = {}
                for row in rows:
                    lang = (row.get("attributes") or {}).get("language") or "?"
                    langs[lang] = langs.get(lang, 0) + 1
                print(f"     LANGUAGE PRESENT, sample: {langs}", flush=True)
            print(f"     relationships: {list(rows[0].get('relationships') or {})}", flush=True)


def probe_language_spread(client: httpx.Client, base: str, limit: int) -> None:
    print()
    print("=" * 72)
    print("4. how dense is it? language spread over a sample of castings")
    print("=" * 72)
    langs: dict[str, int] = {}
    seen = 0
    offset = 0
    while seen < limit:
        data, err = get(
            client,
            f"{base}/castings",
            {
                "filter[isVoiceActor]": "true",
                "page[limit]": 20,
                "page[offset]": offset,
            },
        )
        if err:
            print(f"   stopped at offset {offset}: {err}", flush=True)
            break
        rows = data.get("data") or []
        if not rows:
            break
        for row in rows:
            lang = (row.get("attributes") or {}).get("language") or "(none)"
            langs[lang] = langs.get(lang, 0) + 1
        seen += len(rows)
        offset += len(rows)
    print(f"   sampled {seen} voice-actor castings", flush=True)
    for lang, count in sorted(langs.items(), key=lambda kv: -kv[1]):
        print(f"     {lang:<22} {count:>5}", flush=True)
    total = (data or {}).get("meta", {}).get("count") if data else None
    if total:
        print(f"   meta.count for all voice-actor castings: {total}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe Kitsu as a dub source")
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--raw", action="store_true", help="dump one raw record")
    args = parser.parse_args()

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        base = probe_reachability(client)
        if not base:
            raise SystemExit("Kitsu unreachable at every known host")
        probe_mappings(client, base)
        probe_cast(client, base)
        probe_language_spread(client, base, args.sample)
        if args.raw:
            data, _err = get(
                client, f"{base}/castings", {"filter[isVoiceActor]": "true", "page[limit]": 1}
            )
            print()
            print("raw castings record:")
            print(json.dumps(data, indent=2)[:2000], flush=True)


if __name__ == "__main__":
    main()
