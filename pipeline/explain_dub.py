"""Why is this title not marked as dubbed?

Reported for The Amazing 3 (1965), which has a well known English dub - the
catalog even holds its English release title - and for Discode. Chasing
individual titles by hand neither scales nor generalises, so this asks every
stage in turn and says which one lost it.

Five places a real dub goes missing, and they need different fixes:

  1. The pass never ran for this row. Nothing is wrong; it is pending.
  2. AniList has no cast at all. 38.2% of the catalog is in this state and
     no source in the pipeline can help.
  3. AniList has a cast but no English voice actors. The known false
     negative shape: Berserk (2017) records 21 characters and no English
     cast while having a dub. Only franchise propagation recovers it, and
     only if a relative is dubbed.
  4. ANN has the title but the matcher rejected or missed it. Fixable, and
     the verdict says how - a year or format contradiction, an ambiguous
     name, or no entry under any title the catalog knows.
  5. ANN has no entry either. 43% of the catalog; a hard ceiling.

Several titles per run share one ANN index fetch, which is 22 requests and
by far the slowest part - so diagnose a batch together, not one at a time.

Needs network access to AniList and ANN, so run it where the pipeline runs:

    docker exec mangabrain-worker-1 python -m pipeline.explain_dub "The Amazing 3" Discode
    docker exec mangabrain-worker-1 python -m pipeline.explain_dub --id 1234
"""

import argparse

import httpx
import psycopg
from psycopg.rows import dict_row

from api.config import settings
from pipeline.ann import (
    UA,
    build_index,
    fetch_anime_index,
    fetch_casts,
    match,
    rejected_against,
)
from pipeline.backfill_dub_ann import candidate_titles, dub_languages_for
from pipeline.backfill_dub_languages import CAST_QUERY, languages_for
from pipeline.client import RateLimitedClient

ANILIST_URL = "https://graphql.anilist.co"

LOOKUP_SQL = """
    SELECT id, title_romaji, title_english, title_native, synonyms,
           start_year, format, country_of_origin,
           english_va_count, cast_character_count, english_dub_inferred,
           dub_languages, dub_languages_source, ann_id,
           measured_dub_languages, measured_dub_checked_at, dub_checked_at
    FROM media
    WHERE media_type = 'ANIME'
      AND (title_romaji ILIKE %(q)s
           OR title_english ILIKE %(q)s
           OR title_native ILIKE %(q)s
           OR EXISTS (SELECT 1 FROM unnest(synonyms) s WHERE s ILIKE %(q)s))
    ORDER BY (title_english ILIKE %(exact)s OR title_romaji ILIKE %(exact)s) DESC, id
    LIMIT %(limit)s
"""

BY_ID_SQL = """
    SELECT id, title_romaji, title_english, title_native, synonyms,
           start_year, format, country_of_origin,
           english_va_count, cast_character_count, english_dub_inferred,
           dub_languages, dub_languages_source, ann_id,
           measured_dub_languages, measured_dub_checked_at, dub_checked_at
    FROM media
    WHERE media_type = 'ANIME' AND id = ANY(%(ids)s)
    ORDER BY id
"""


def rule(text: str) -> None:
    print("=" * 72)
    print(text)
    print("=" * 72)


def show_database(row: dict) -> list[str]:
    """Print what the database holds. Returns notes for the final verdict."""
    rule(f"1. what the database holds for #{row['id']}")
    print(f"   romaji          {row['title_romaji']}")
    print(f"   english         {row['title_english']}")
    print(f"   year / format   {row['start_year']} / {row['format']} / {row['country_of_origin']}")
    print(f"   synonyms        {(row['synonyms'] or [])[:6]}")
    print()
    en = row["english_va_count"]
    print(f"   english_va_count      {en if en is not None else 'NULL'}")
    print(f"   cast_character_count  {row['cast_character_count']}")
    print(f"   english_dub_inferred  {row['english_dub_inferred']}")
    print(f"   dub_languages (ANN)   {row['dub_languages']}  source={row['dub_languages_source']}")
    print(f"   ann_id                {row['ann_id']}")
    print(f"   measured (AniList)    {row['measured_dub_languages']}")
    print(
        f"   checked: english_pass={bool(row['dub_checked_at'])}"
        f"  language_pass={bool(row['measured_dub_checked_at'])}"
    )

    notes: list[str] = []
    # Case 1 before anything else: an unrun pass is not a data gap, and
    # saying "AniList has no English cast" about a row nothing ever asked
    # about would be the same absence-as-evidence mistake the dub filter is
    # built to avoid.
    if not row["dub_checked_at"] and not row["measured_dub_checked_at"]:
        notes.append(
            "PENDING: neither AniList pass has visited this row. Not a gap -"
            " run backfill_dub_languages and look again."
        )
    elif not row["measured_dub_checked_at"]:
        notes.append(
            "PARTIAL: the all-language walk has not reached this row;"
            " only the English-only pass has."
        )
    if row["dub_languages_source"] is None and row["ann_id"] is None:
        notes.append("the ANN pass has not visited this row either.")
    return notes


def ask_anilist(client: RateLimitedClient, media_id: int) -> list[str]:
    print()
    rule("2. what AniList says right now")
    payload = client.request(
        "POST",
        ANILIST_URL,
        json_body={"query": CAST_QUERY, "variables": {"ids": [media_id], "perPage": 1}},
    )
    if payload.get("errors"):
        print(f"   errors: {payload['errors']}", flush=True)
        return ["AniList returned errors; stage unknown."]
    media_list = ((payload.get("data") or {}).get("Page") or {}).get("media") or []
    if not media_list:
        print("   AniList returned nothing for this id", flush=True)
        return ["AniList no longer serves this id."]

    media = media_list[0]
    edges = (media.get("characters") or {}).get("edges") or []
    actors = [a for e in edges for a in (e.get("voiceActors") or [])]
    print(f"   characters listed      {len(edges)}")
    print(f"   voice actors credited  {len(actors)}")

    if not edges:
        print(
            "\n   -> NO CAST AT ALL. The 38.2% case: AniList has no voice"
            "\n      actors here, so english_va_count = 0 is an absence of"
            "\n      data, not evidence of no dub.",
            flush=True,
        )
        return ["AniList has no cast whatsoever (case 2). Only ANN can answer for this title."]

    codes, unknown = languages_for(media)
    print(f"   dub languages found    {codes or 'none (after stripping the native one)'}")
    if unknown:
        print(f"   unmapped languageV2    {unknown}")
    if not actors:
        return ["AniList lists characters but no voice actors in any language (case 2)."]
    if "en" in codes:
        print(
            "\n   -> AniList DOES credit an English cast. The database above"
            "\n      should already show it; if it does not, the language walk"
            "\n      has not reached this row yet.",
            flush=True,
        )
        return ["AniList credits an English cast right now - re-run the language walk (case 1)."]
    print(
        "\n   -> cast present, NO ENGLISH. The Berserk (2017) shape: a real"
        "\n      dub whose English cast was never entered upstream.",
        flush=True,
    )
    return [
        "AniList has a cast but no English in it (case 3)."
        " Recoverable only via ANN or a dubbed franchise relative."
    ]


def ann_cast(client: httpx.Client, entry, country: str | None) -> tuple[int, list[str]]:
    casts = fetch_casts(client, [entry.ann_id])
    english, langs = casts.get(entry.ann_id, (0, set()))
    return english, dub_languages_for(langs, country)


def ask_ann(client: httpx.Client, index: dict, row: dict) -> list[str]:
    print()
    rule("3. what ANN says, and what the matcher did with it")
    names = candidate_titles(row)
    print(f"   trying {len(names)} candidate titles:")
    for name in names[:8]:
        print(f"     - {name}")

    verdict, entry = match(index, names, year=row["start_year"], fmt=row["format"])
    print(f"\n   verdict: {verdict}")

    if verdict == "exact" and entry is not None:
        print(f"   entry:   ann_id={entry.ann_id} {entry.name!r} {entry.kind} {entry.year}")
        english, langs = ann_cast(client, entry, row["country_of_origin"])
        print(f"   ANN dub languages: {langs or 'none recorded'}  (english cast size {english})")
        if "en" in langs:
            return [
                f"ANN KNOWS about an English dub (ann_id {entry.ann_id})."
                " The ANN pass has not written it to this row yet (case 1)."
            ]
        return [f"ANN matches (ann_id {entry.ann_id}) but records no English cast either."]

    if verdict.startswith("rejected_"):
        guard = verdict.split("_", 1)[1]
        # match() withholds the entry on a rejection on purpose, so that a
        # rejected match can never be stored by mistake. Diagnosing one still
        # needs to see it, which is what rejected_against is for.
        blocked = rejected_against(index, names, year=row["start_year"], fmt=row["format"])
        if blocked is not None:
            print(
                f"   rejected against: ann_id={blocked.ann_id} {blocked.name!r}"
                f" {blocked.kind} {blocked.year}"
            )
            print(f"   catalog says:     {row['format']} {row['start_year']}")
            english, langs = ann_cast(client, blocked, row["country_of_origin"])
            print(f"   its dub languages: {langs or 'none recorded'}  (english {english})")
            cost = (
                " It does hold an English dub, so this guard cost a real match."
                if ("en" in langs)
                else " It records no English dub, so the guard cost nothing here."
            )
            return [
                f"ANN has a name match but the {guard} guard rejected it"
                f" ({blocked.kind} {blocked.year} vs {row['format']} {row['start_year']})."
                f"{cost}"
            ]
        print(
            f"\n   -> the {guard} guard rejected a name match. That guard exists"
            "\n      because a 2-character Suntory advert once matched 'your"
            "\n      name.' and was stored as a dub, but it costs real matches"
            "\n      too - 1,228 titles catalog-wide are rejected on format.",
            flush=True,
        )
        return [f"ANN name match rejected by the {guard} guard (case 4)."]

    if verdict.startswith("ambiguous_"):
        print(
            "\n   -> several ANN entries share this name and none could be"
            "\n      chosen. Writing either would be a guess.",
            flush=True,
        )
        return ["ANN has several same-named entries; the matcher refused to guess (case 4)."]

    if verdict == "base":
        print(f"   parent entry: ann_id={entry.ann_id} {entry.name!r} {entry.kind} {entry.year}")
        english, langs = ann_cast(client, entry, row["country_of_origin"])
        print(f"   parent dub languages: {langs or 'none recorded'}  (english {english})")
        print(
            "\n   -> matched a PARENT series only, which is deliberately not"
            "\n      written: a parent's dub languages are not this entry's."
            "\n      Franchise propagation is the route for this one.",
            flush=True,
        )
        return [
            "ANN resolves only the parent series"
            + (
                ", which does hold an English dub - propagation territory."
                if "en" in langs
                else "."
            )
        ]

    print(
        "\n   -> no ANN entry under any title the catalog knows. Either ANN"
        "\n      files it under a name AniList does not list, or it has no"
        "\n      entry at all (43% of the catalog).",
        flush=True,
    )
    return ["no ANN entry under any known title (case 5)."]


def main() -> None:
    parser = argparse.ArgumentParser(description="Explain a title's dub status")
    parser.add_argument("titles", nargs="*", help="titles to look up (ILIKE match)")
    parser.add_argument(
        "--id", type=int, action="append", default=[], help="AniList id; repeatable"
    )
    parser.add_argument("--limit", type=int, default=3, help="rows per title match (default 3)")
    parser.add_argument("--skip-ann", action="store_true", help="skip the ANN index fetch")
    args = parser.parse_args()
    if not args.titles and not args.id:
        raise SystemExit("give one or more titles, or --id")

    rows: list[dict] = []
    seen: set[int] = set()
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        if args.id:
            rows += conn.execute(BY_ID_SQL, {"ids": args.id}).fetchall()
        for title in args.titles:
            found = conn.execute(
                LOOKUP_SQL,
                {"q": f"%{title}%", "exact": title, "limit": args.limit},
            ).fetchall()
            if not found:
                print(f"no anime matched {title!r}")
            rows += found
    rows = [r for r in rows if not (r["id"] in seen or seen.add(r["id"]))]
    if not rows:
        raise SystemExit("nothing to explain")

    index: dict = {}
    ann_client: httpx.Client | None = None
    if not args.skip_ann:
        ann_client = httpx.Client(timeout=90.0, follow_redirects=True, headers={"User-Agent": UA})
        # Once for the whole run: this is 22 requests and the slowest step.
        print("fetching ANN's anime index (22 requests, shared by every title)...", flush=True)
        entries = fetch_anime_index(ann_client)
        index = build_index(entries)
        print(f"{len(entries)} ANN entries indexed\n", flush=True)

    anilist = RateLimitedClient(min_interval=settings.anilist_min_interval)
    verdicts: list[tuple[dict, list[str]]] = []
    try:
        for row in rows:
            notes = show_database(row)
            notes += ask_anilist(anilist, row["id"])
            if ann_client is not None:
                notes += ask_ann(ann_client, index, row)
            verdicts.append((row, notes))
            print()
    finally:
        if ann_client is not None:
            ann_client.close()

    rule("verdict")
    for row, notes in verdicts:
        print(f"\n#{row['id']} {row['title_english'] or row['title_romaji']}")
        for note in notes:
            print(f"   - {note}")


if __name__ == "__main__":
    main()
