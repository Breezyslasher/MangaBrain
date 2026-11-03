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

The API's real contract, measured by pipeline.probe_kitsu_shape rather than
read from docs - the OpenAPI spec documents 8 paths and no cast resources,
the legacy Apiary docs no longer resolve, and api-docs.kitsu.cloud is a
different product (CGWire's Kitsu, an animation tracker whose "casting"
means assigning assets to shots). Each of these was guessed wrong at least
once:

  - Filters are normalised server-side. filter[mediaId] and filter[media_id]
    both work and return the same count; errors report the snake_case form.
    The camelCase spelling was never the problem.
  - voiceActor is NOT filterable: "voice_actor is not allowed". Selecting on
    it must happen client-side.
  - Comma lists DO work: filter[mediaId]=1,2,3 is honoured. An earlier run
    logged "bulk castings: HTTP 400" and blamed the comma list, but that
    request also carried the invalid voiceActor filter, which is what the
    400 was about. Batched fetching was available all along.
  - media-characters cannot be filtered by media in either spelling; reach
    characters via /anime/{id}/characters.
  - page[limit] is hard-capped at 20: "Limit exceeds maximum page size of 20".
  - mappings takes a comma list of externalId, holds 341,771 rows, and
    accepts both anilist/anime and myanimelist/anime as externalSite.

And the resource question: castings carries a free-text `language`
("Brazilian", "English"), while character-voices carries a `locale` code
(ja_jp, en, it, es), holds 207,844 rows, and is reachable only through
/anime/{id}/characters?include=voices. Cowboy Bebop has 345 castings against
roughly 600 character-voices, so character-voices is denser and cleaner, and
every language figure this probe reports comes from castings and is a FLOOR.
"""

import argparse
import json
import random
import time

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
                "filter[voiceActor]": "true",
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


def probe_bulk(client: httpx.Client, base: str) -> None:
    """Can the join and the cast fetch be batched?

    One mappings call plus one castings call per title is 45,000 requests for
    the catalog, which decides whether this is a nightly pass or a weekend.
    JSON:API filters often accept a comma list, so it is worth one request to
    find out rather than designing around the pessimistic case.
    """
    print()
    print("=" * 72)
    print("4. can the join and the cast fetch be batched?")
    print("=" * 72)
    ids = ",".join(str(i) for i in KNOWN_ANILIST)
    data, err = get(
        client,
        f"{base}/mappings",
        {
            "filter[externalSite]": "anilist/anime",
            "filter[externalId]": ids,
            "include": "item",
            "page[limit]": 20,
        },
    )
    if err:
        print(f"   bulk mappings ({len(KNOWN_ANILIST)} ids): {err}", flush=True)
    else:
        rows = data.get("data") or []
        got = {(r.get("attributes") or {}).get("externalId") for r in rows}
        print(
            f"   bulk mappings: asked {len(KNOWN_ANILIST)} ids, got {len(rows)} rows"
            f" covering {len(got)} distinct externalIds",
            flush=True,
        )
        if len(got) >= len(KNOWN_ANILIST):
            print("     BATCHED JOIN WORKS: a comma list is honoured", flush=True)
        else:
            print("     comma list not honoured; the join is one request per title", flush=True)

    data, err = get(
        client,
        f"{base}/castings",
        {"filter[mediaId]": "1,2,3", "filter[voiceActor]": "true", "page[limit]": 20},
    )
    if err:
        print(f"   bulk castings: {err}", flush=True)
    else:
        media_ids = set()
        for row in data.get("data") or []:
            rel = ((row.get("relationships") or {}).get("media") or {}).get("data") or {}
            if rel.get("id"):
                media_ids.add(rel["id"])
        print(
            f"   bulk castings for 3 media ids: {len(data.get('data') or [])} rows"
            f" spanning media {sorted(media_ids) or '-'}",
            flush=True,
        )


def probe_density(client: httpx.Client, base: str, sample: int) -> None:
    """Mapping resolution rate and language spread over real AniList ids.

    The point of comparison is not Kitsu's totals but what it yields for the
    ids we actually hold, which is what ANN's 31.8% match rate measures.
    """
    print()
    print("=" * 72)
    print("5. resolution rate and language spread over sampled AniList ids")
    print("=" * 72)
    anilist_ids = fetch_anilist_ids(client, sample)
    if not anilist_ids:
        print("   could not sample AniList ids", flush=True)
        return
    print(f"   sampled {len(anilist_ids)} AniList anime ids", flush=True)

    resolved = 0
    with_cast = 0
    cast_failures = 0
    map_failures = 0
    castings_seen = 0
    truncated_titles = 0
    langs: dict[str, int] = {}
    titles_per_lang: dict[str, int] = {}
    for anilist_id in anilist_ids:
        data, err = get(
            client,
            f"{base}/mappings",
            {
                "filter[externalSite]": "anilist/anime",
                "filter[externalId]": str(anilist_id),
                "include": "item",
                "page[limit]": 1,
            },
        )
        if err or not (data.get("data") or []):
            continue
        item = next((i for i in (data.get("included") or []) if i.get("type") == "anime"), None)
        if not item:
            continue
        resolved += 1
        # voiceActor is an attribute, NOT a filterable one: filtering on it
        # returns HTTP 400, which is how this step once reported "0/200 had
        # a cast" when what actually happened was 200 failed requests.
        # Fetch unfiltered and select locally.
        #
        # And paginate. Kitsu caps page[limit] at 20 while Cowboy Bebop has
        # 345 castings, so reading one page measured the first 20 only - with
        # Japanese crowding the early rows at 8.2 credits per title, that
        # undercounted every dub language by an unknown margin. The counts
        # were lower bounds being read as coverage.
        seen_here: set[str] = set()
        offset = 0
        truncated = False
        failed = False
        while offset < MAX_CASTINGS:
            cast, err = get(
                client,
                f"{base}/castings",
                {
                    "filter[mediaId]": item["id"],
                    "page[limit]": 20,
                    "page[offset]": offset,
                },
            )
            if err:
                failed = True
                break
            rows = cast.get("data") or []
            for row in rows:
                attrs = row.get("attributes") or {}
                if not attrs.get("voiceActor"):
                    continue
                lang = attrs.get("language") or "(none)"
                langs[lang] = langs.get(lang, 0) + 1
                seen_here.add(lang)
            castings_seen += len(rows)
            if len(rows) < 20:
                break
            offset += len(rows)
        else:
            truncated = True
        if failed:
            cast_failures += 1
            continue
        if truncated:
            truncated_titles += 1
        if seen_here:
            with_cast += 1
        for lang in seen_here:
            titles_per_lang[lang] = titles_per_lang.get(lang, 0) + 1

    n = len(anilist_ids)
    print(
        f"   resolved via mappings    {resolved:>4}/{n}  ({100 * resolved / n:.1f}%)"
        f"{f'  [{map_failures} request failures]' if map_failures else ''}",
        flush=True,
    )
    # A rate computed over failed requests is not a measurement. This step
    # has already once reported 0.0% when every request behind it 400'd, so
    # it says so instead of averaging the failures in.
    if cast_failures:
        print(
            f"   CANNOT REPORT cast coverage: {cast_failures}/{resolved} castings"
            " requests failed. Fix the request before reading this number.",
            flush=True,
        )
    elif resolved:
        print(
            f"   of those, had a VA cast  {with_cast:>4}/{resolved}"
            f"  ({100 * with_cast / resolved:.1f}%)",
            flush=True,
        )
    else:
        print("   none resolved", flush=True)
    print(
        f"   castings read            {castings_seen:>5}"
        f"  ({castings_seen / resolved:.1f} per resolved title)"
        if resolved
        else "",
        flush=True,
    )
    if truncated_titles:
        print(
            f"   NOTE {truncated_titles} title(s) hit the {MAX_CASTINGS}-casting"
            " bound, so their later languages are unread",
            flush=True,
        )
    print("   titles per language (fully paginated):", flush=True)
    for lang, count in sorted(titles_per_lang.items(), key=lambda kv: -kv[1]):
        share = 100.0 * count / resolved if resolved else 0.0
        print(
            f"     {lang:<22} {count:>4} titles ({share:>4.1f}%), {langs[lang]:>5} credits",
            flush=True,
        )
    print(
        "\n   Compare: ANN resolved 31.8% of the catalog by name. This number"
        "\n   is the id-join equivalent, and it has no wrong-match risk.",
        flush=True,
    )


ANILIST_IDS_QUERY = """
query ($ids: [Int]) {
  Page(page: 1, perPage: 50) {
    media(id_in: $ids, type: ANIME) { id }
  }
}
"""

# Kitsu caps page[limit] at 20, so a title with hundreds of castings needs
# many requests. Bound it, and report how many titles hit the bound rather
# than quietly truncating them as the previous version did.
MAX_CASTINGS = 200


def fetch_anilist_ids(client: httpx.Client, sample: int) -> list[int]:
    """A sample of the whole catalog, not of its popular head.

    Sorting by popularity cannot reach past AniList's 5000-row offset cap, so
    the earlier version measured the top ~5,000 titles and nothing else. Dub
    coverage collapses in the tail - AniList English runs near 100% at the
    head and 0% in the least popular strata - so a head sample overstates
    every source, which is exactly how the ANN estimate came out at double
    the measured rate.

    Random ids across the id space are drawn instead, then filtered to the
    ones AniList serves as anime, which is the same trick full_sync uses.
    """
    out: list[int] = []
    rng = random.Random(20261103)
    while len(out) < sample:
        ids = [rng.randint(1, 190_000) for _ in range(50)]
        try:
            resp = client.post(
                "https://graphql.anilist.co",
                json={"query": ANILIST_IDS_QUERY, "variables": {"ids": ids}},
            )
            media = ((resp.json().get("data") or {}).get("Page") or {}).get("media") or []
        except (httpx.HTTPError, ValueError):
            break
        out.extend(m["id"] for m in media)
        time.sleep(2.0)
    return out[:sample]


# Measured, not assumed: the castings pass ran at 2.4 requests/second with
# zero failures while the voices pass ran at 6.8 and lost 10% of them. The
# castings path was never paced deliberately - it was slower by accident of
# doing more work per request. Kitsu publishes no rate limit in its spec, so
# this holds to the rate that demonstrably worked.
MIN_INTERVAL = 0.4
_last_request = [0.0]


def get_paced(client: httpx.Client, url: str, params: dict | None = None):
    """get() with pacing and a retry, so a 429 is waited out not counted."""
    for attempt in range(4):
        wait = MIN_INTERVAL - (time.monotonic() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()
        try:
            resp = client.get(url, params=params, headers=HEADERS)
        except httpx.HTTPError as exc:
            if attempt == 3:
                return None, f"{type(exc).__name__}: {exc}"
            time.sleep(2**attempt)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 2**attempt
            if attempt == 3:
                return None, f"HTTP {resp.status_code} after {attempt + 1} tries"
            time.sleep(min(delay, 30.0))
            continue
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}: {resp.text[:160]}"
        try:
            return resp.json(), None
        except ValueError:
            return None, "not JSON"
    return None, "gave up"


def normalise_locale(locale: str) -> str:
    """ja_jp -> ja, pt_br -> pt-br. Kitsu mixes bare codes and locales."""
    code = locale.strip().lower().replace("_", "-")
    if not code:
        return "(none)"
    head, _, tail = code.partition("-")
    # Keep a region only where it distinguishes a real dub market, as
    # Portuguese/Brazilian does; otherwise collapse to the language.
    if head in {"pt", "zh"} and tail:
        return f"{head}-{tail}"
    return head


def voices_for_anime(
    client: httpx.Client, base: str, kitsu_id: str
) -> tuple[set[str], int, str | None]:
    """Every cast locale for one anime, via the include path.

    castings carries a free-text `language` ("Brazilian"); character-voices
    carries a `locale` code and is denser - 345 castings against roughly 600
    character-voices on Cowboy Bebop. character-voices cannot be filtered by
    media or locale at all ("not allowed"), so the only route in is
    /anime/{id}/characters?include=voices, paginated at the hard cap of 20.
    """
    locales: set[str] = set()
    seen = 0
    offset = 0
    total: int | None = None
    while offset < MAX_CASTINGS:
        data, err = get_paced(
            client,
            f"{base}/anime/{kitsu_id}/characters",
            {"include": "voices", "page[limit]": 20, "page[offset]": offset},
        )
        if err:
            # A 404 here is an absence, not a breakage: Kitsu returns it for
            # an anime with no characters, and for an offset past the end.
            # Counting those as failures is what blocked the whole
            # measurement on 16 of 164 titles - all of them 404, the same
            # count before and after pacing, which is how it became clear
            # that throttling was never involved.
            if err.startswith("HTTP 404"):
                return locales, seen, None
            return locales, seen, err
        rows = data.get("data") or []
        for item in data.get("included") or []:
            if item.get("type") != "characterVoices":
                continue
            raw = (item.get("attributes") or {}).get("locale")
            if raw:
                locales.add(normalise_locale(raw))
                seen += 1
        if total is None:
            total = (data.get("meta") or {}).get("count")
        offset += len(rows)
        # Bound by the reported total rather than by "a full page came back",
        # which asked for one page past the end on every title whose
        # character count is an exact multiple of the page size.
        if not rows or (total is not None and offset >= total):
            break
    return locales, seen, None


def resolve_mappings(client: httpx.Client, base: str, anilist_ids: list[int]) -> dict[int, str]:
    """AniList id -> Kitsu anime id, batched.

    filter[externalId] honours a comma list, which an earlier run wrote off
    after a 400 that was actually caused by an invalid voiceActor filter in
    the same request. page[limit] caps at 20, so that is the batch size.
    """
    out: dict[int, str] = {}
    for start in range(0, len(anilist_ids), 20):
        batch = anilist_ids[start : start + 20]
        data, err = get(
            client,
            f"{base}/mappings",
            {
                "filter[externalSite]": "anilist/anime",
                "filter[externalId]": ",".join(str(i) for i in batch),
                "include": "item",
                "page[limit]": 20,
            },
        )
        if err:
            continue
        items = {i["id"]: i for i in (data.get("included") or []) if i.get("type") == "anime"}
        for row in data.get("data") or []:
            ext = (row.get("attributes") or {}).get("externalId")
            rel = ((row.get("relationships") or {}).get("item") or {}).get("data") or {}
            if ext and rel.get("id") in items:
                try:
                    out[int(ext)] = rel["id"]
                except (TypeError, ValueError):
                    continue
    return out


def probe_voices(client: httpx.Client, base: str, sample: int) -> None:
    """The same measurement as step 5, through character-voices instead.

    Deliberately over the SAME sampled ids, because the castings figures and
    these differ only by resource - two separate samples would confound the
    resource with the draw, which is how the head-biased run overstated
    everything by a factor of three.
    """
    print()
    print("=" * 72)
    print("6. the same titles through character-voices, not castings")
    print("=" * 72)
    anilist_ids = fetch_anilist_ids(client, sample)
    if not anilist_ids:
        print("   could not sample AniList ids", flush=True)
        return
    resolved = resolve_mappings(client, base, anilist_ids)
    print(
        f"   sampled {len(anilist_ids)} ids, resolved {len(resolved)}"
        f" ({100 * len(resolved) / len(anilist_ids):.1f}%) via batched mappings",
        flush=True,
    )

    titles_per_locale: dict[str, int] = {}
    credits_per_locale: dict[str, int] = {}
    with_voices = 0
    failures = 0
    truncations = 0
    total_credits = 0
    reasons: dict[str, int] = {}
    for kitsu_id in resolved.values():
        locales, seen, err = voices_for_anime(client, base, kitsu_id)
        if err:
            failures += 1
            key = err.split(":")[0][:40]
            reasons[key] = reasons.get(key, 0) + 1
            continue
        if seen >= MAX_CASTINGS:
            truncations += 1
        total_credits += seen
        if locales:
            with_voices += 1
        for code in locales:
            titles_per_locale[code] = titles_per_locale.get(code, 0) + 1
            credits_per_locale[code] = credits_per_locale.get(code, 0) + seen

    n = len(resolved)
    if failures:
        print(
            f"   CANNOT REPORT: {failures}/{n} character requests failed."
            " Fix the request before reading these numbers.",
            flush=True,
        )
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"     {reason:<44} {count:>4}", flush=True)
        return
    print(f"   voice credits read       {total_credits:>5}", flush=True)
    print(
        f"   titles with any voices   {with_voices:>5}/{n}  ({100 * with_voices / n:.1f}%)"
        if n
        else "   nothing resolved",
        flush=True,
    )
    if truncations:
        print(f"   NOTE {truncations} title(s) hit the {MAX_CASTINGS} bound", flush=True)
    print("   titles per locale:", flush=True)
    for code, count in sorted(titles_per_locale.items(), key=lambda kv: -kv[1]):
        share = 100.0 * count / n if n else 0.0
        print(f"     {code:<10} {count:>4} titles ({share:>4.1f}%)", flush=True)
    print(
        "\n   Step 5 measured the same titles through castings. A locale here"
        "\n   that beats its castings language is the floor lifting; one that"
        "\n   does not means castings was not the limitation.",
        flush=True,
    )


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
        probe_bulk(client, base)
        probe_density(client, base, args.sample)
        probe_voices(client, base, args.sample)
        if args.raw:
            data, _err = get(
                client, f"{base}/castings", {"filter[voiceActor]": "true", "page[limit]": 1}
            )
            print()
            print("raw castings record:")
            print(json.dumps(data, indent=2)[:2000], flush=True)


if __name__ == "__main__":
    main()
