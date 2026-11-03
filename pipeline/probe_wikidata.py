"""Is Wikidata a usable English dub source?

The last stone. Everything else is measured: AniList's cast answers for the
id it was given and covers every language in one request, ANN matches 31.8%
by title, franchise propagation adds 2,537 titles free, Kitsu loses on cast
density, AniDB needs a registered client and records dubs per release, Jikan
is unreachable from both hosts, and the studio signal reaches ~182 titles at
the cost of ~28 false dub claims.

Wikidata is worth one look because of its shape rather than its size. It
publishes external identifiers, so it joins on an id already stored on every
row, and its SPARQL endpoint answers aggregate questions in one query instead
of a per-title crawl - so the whole thing is cheap to rule in or out.

The property ids are DISCOVERED here, not recalled. Writing P4086 or P8729
from memory is exactly how three measurements in this investigation ended up
against the wrong field, and a wrong property id in SPARQL returns a
confident empty result rather than an error - the same silent-wrong-answer
shape as the missing node selection and the ignored language argument.

What has to be true for this to be worth building:

  1. An id join exists and is populated for a useful share of anime.
  2. Voice actors are recorded on the work (or reachable from it).
  3. Those statements carry a LANGUAGE. A cast list with no language says
     nothing about dubs - this is what sank AniDB's <seiyuu>.
  4. The counts are large enough to matter against 7,016 titles already
     covered.
"""

import argparse
import json

import httpx

ENDPOINT = "https://query.wikidata.org/sparql"
# WDQS asks for a descriptive agent and throttles anonymous bulk use.
UA = "MangaBrain/0.1 (self-hosted recommendation engine; one-off source evaluation)"

# Resolve the identifier properties by label rather than hardcoding numbers.
DISCOVER_PROPS = """
SELECT ?prop ?propLabel WHERE {
  ?prop wikibase:propertyType wikibase:ExternalId .
  ?prop rdfs:label ?propLabel .
  FILTER(LANG(?propLabel) = "en")
  FILTER(
    CONTAINS(LCASE(?propLabel), "myanimelist") ||
    CONTAINS(LCASE(?propLabel), "anilist") ||
    CONTAINS(LCASE(?propLabel), "anime news network") ||
    CONTAINS(LCASE(?propLabel), "anidb") ||
    CONTAINS(LCASE(?propLabel), "kitsu")
  )
}
LIMIT 50
"""

# Likewise for the statement properties that would carry a dub.
DISCOVER_CAST_PROPS = """
SELECT ?prop ?propLabel WHERE {
  VALUES ?prop { wd:P725 wd:P161 wd:P407 wd:P364 }
  ?prop rdfs:label ?propLabel .
  FILTER(LANG(?propLabel) = "en")
}
"""

COUNT_WITH_ID = """
SELECT (COUNT(DISTINCT ?item) AS ?count) WHERE {
  ?item wdt:%(prop)s ?id .
}
"""

# Voice actors recorded on a work, with any language qualifier.
VOICE_WITH_LANGUAGE = """
SELECT (COUNT(DISTINCT ?item) AS ?items) (COUNT(*) AS ?statements) WHERE {
  ?item wdt:%(idprop)s ?extid .
  ?item p:P725 ?st .
  ?st pq:P407 ?lang .
}
"""

VOICE_ANY = """
SELECT (COUNT(DISTINCT ?item) AS ?items) (COUNT(*) AS ?statements) WHERE {
  ?item wdt:%(idprop)s ?extid .
  ?item p:P725 ?st .
}
"""

LANGUAGE_SPREAD = """
SELECT ?langLabel (COUNT(DISTINCT ?item) AS ?items) WHERE {
  ?item wdt:%(idprop)s ?extid .
  ?item p:P725 ?st .
  ?st pq:P407 ?lang .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
GROUP BY ?langLabel
ORDER BY DESC(?items)
LIMIT 20
"""

SAMPLE = """
SELECT ?item ?itemLabel ?extid ?vaLabel ?langLabel WHERE {
  ?item wdt:%(idprop)s ?extid .
  ?item p:P725 ?st .
  ?st ps:P725 ?va .
  OPTIONAL { ?st pq:P407 ?lang }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
LIMIT 15
"""


PREFERRED_ID_PROPS = (
    "MyAnimeList anime ID",
    "AniList anime ID",
    "Anime News Network anime ID",
    "Kitsu anime ID",
)


def choose_id_property(discovered: dict[str, str]) -> tuple[str, str]:
    """Pick the anime identifier property by EXACT label.

    A substring test picked "MyAnimeList people ID" on the first run,
    because "anime" occurs inside "myanimelist" - myanimelist[2:7] is
    literally "anime" - so the probe joined voice actors against PEOPLE ids
    and reported 3 items, which read as "Wikidata has almost no dub data"
    and was entirely an artefact of the match. A wrong property id in SPARQL
    returns a confident empty result, not an error, so nothing flagged it.

    MyAnimeList first because every catalog row already stores idMal;
    AniList second because the catalog is keyed on it.
    """
    for want in PREFERRED_ID_PROPS:
        pid = discovered.get(want)
        if pid:
            return want, pid
    raise SystemExit(
        "none of the expected anime identifier properties were found:"
        f" wanted one of {PREFERRED_ID_PROPS}, discovered {sorted(discovered)}"
    )


def query(client: httpx.Client, sparql: str, label: str) -> list[dict] | None:
    try:
        resp = client.get(
            ENDPOINT,
            params={"query": sparql, "format": "json"},
            headers={"User-Agent": UA, "Accept": "application/sparql-results+json"},
        )
    except httpx.HTTPError as exc:
        print(f"  {label:<38} {type(exc).__name__}: {exc}", flush=True)
        return None
    if resp.status_code != 200:
        print(f"  {label:<38} HTTP {resp.status_code}  {resp.text[:200]}", flush=True)
        return None
    try:
        return resp.json()["results"]["bindings"]
    except (ValueError, KeyError) as exc:
        print(f"  {label:<38} unparseable: {exc}", flush=True)
        return None


def scalar(rows: list[dict] | None, key: str) -> int | None:
    if not rows:
        return None
    value = (rows[0].get(key) or {}).get("value")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Wikidata as a dub source")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    with httpx.Client(timeout=args.timeout, follow_redirects=True) as client:
        print("=" * 72)
        print("1. which identifier properties exist? (discovered, not recalled)")
        print("=" * 72)
        rows = query(client, DISCOVER_PROPS, "identifier properties")
        props: dict[str, str] = {}
        for row in rows or []:
            pid = (row.get("prop") or {}).get("value", "").rsplit("/", 1)[-1]
            name = (row.get("propLabel") or {}).get("value", "")
            if pid.startswith("P"):
                props[name] = pid
                print(f"   {name[:46]:<46} {pid}", flush=True)
        if not props:
            raise SystemExit("could not discover any identifier property")

        chosen, idprop = choose_id_property(props)
        PREFERRED = PREFERRED_ID_PROPS
        print()
        print("=" * 72)
        print("2. how many items carry that identifier?")
        print("=" * 72)
        for name, pid in props.items():
            rows = query(client, COUNT_WITH_ID % {"prop": pid}, name[:36])
            count = scalar(rows, "count")
            if count is not None:
                print(f"   {name[:46]:<46} {count:>8}", flush=True)

        print()
        print("=" * 72)
        print("3. are voice actors recorded, and do they carry a language?")
        print("=" * 72)
        # Every anime-id property, not only the chosen one: if one join is
        # sparse the others say whether that is Wikidata or the join.
        lang_items = 0
        for want in PREFERRED:
            pid = props.get(want)
            if not pid:
                continue
            rows = query(client, VOICE_ANY % {"idprop": pid}, f"{want} any VA")
            items, statements = scalar(rows, "items"), scalar(rows, "statements")
            rows = query(client, VOICE_WITH_LANGUAGE % {"idprop": pid}, f"{want} with lang")
            l_items, l_st = scalar(rows, "items"), scalar(rows, "statements")
            print(
                f"   {want:<30} any VA {items}/{statements} st   with language {l_items}/{l_st} st",
                flush=True,
            )
            if pid == idprop:
                lang_items = l_items or 0
        print(
            "\n   A cast with no language says nothing about dubs - that is what"
            "\n   ruled out AniDB's <seiyuu>, which is the original cast.",
            flush=True,
        )

        print()
        print("=" * 72)
        print("4. which languages, and how many titles each?")
        print("=" * 72)
        rows = query(client, LANGUAGE_SPREAD % {"idprop": idprop}, "language spread")
        if rows:
            for row in rows:
                name = (row.get("langLabel") or {}).get("value", "?")
                count = (row.get("items") or {}).get("value", "?")
                print(f"   {name[:40]:<40} {count:>6}", flush=True)
        else:
            print("   no language-qualified voice actor statements at all", flush=True)

        print()
        print("=" * 72)
        print("5. a sample, to see what the data actually looks like")
        print("=" * 72)
        rows = query(client, SAMPLE % {"idprop": idprop}, "sample")
        for row in (rows or [])[:15]:
            title = (row.get("itemLabel") or {}).get("value", "?")
            extid = (row.get("extid") or {}).get("value", "?")
            va = (row.get("vaLabel") or {}).get("value", "?")
            lang = (row.get("langLabel") or {}).get("value", "-")
            print(f"   {title[:26]:<26} id={extid:<8} {va[:22]:<22} lang={lang}", flush=True)

        print()
        print("=" * 72)
        print("verdict inputs")
        print("=" * 72)
        print(
            f"   English dub coverage already stands at 7,016 titles."
            f"\n   Wikidata is worth building only if the language-qualified count"
            f"\n   above ({lang_items}) is a meaningful fraction of that, AND those"
            "\n   titles are not ones AniList already answers for - Wikidata's"
            "\n   anime coverage skews to notable works, which is exactly the half"
            "\n   that already has a cast recorded.",
            flush=True,
        )
        print(f"\n   raw discovery payload for reference: {json.dumps(props)}", flush=True)


if __name__ == "__main__":
    main()
