"""Does MAL know about dubs AniList and ANN miss, especially obscure ones?

The one major candidate never actually measured. The cross-check was built
(probe_dub_coverage.jikan_has_english_va) and then abandoned: api.jikan.moe
refused GitHub's runners outright and the note in probe_dub_sources records
it failing from the deployment host too, so no number was ever produced.
That premise is worth retesting rather than inheriting, because the MAL list
sync in api/routers/mal.py goes through Jikan from that same host - if list
refresh works there, Jikan is reachable and the blocker was stale.

Why it is the strongest remaining candidate, on structure rather than hope:

  - It joins on id_mal, stored on every row. No title matching at all, so
    none of the wrong-match risk that caps ANN - ANN resolves 31.8% by name
    and can in principle assert another show's dub, which is why its guards
    exist. MAL needs no guards.
  - /anime/{id}/characters returns voice_actors[].language, the same shape as
    AniList's cast, so it answers for every language and not only English.
  - Its userbase is the English-speaking one, and dub credits are exactly
    what that userbase enters. AniList's English coverage falls off with
    obscurity (100% at the top, ~20% at rank 5000); there is no a priori
    reason MAL's falls off the same way.

MEASURED, 5 Nov 2026: Tenrai (api.tenrai.org/v1) answers this probe's
exact request shape. Cowboy Bebop returns English, French, German,
Hungarian, Italian, Japanese, Korean, Portuguese (BR) and Spanish, in one
second, read by the unmodified voice_actors[].language parser below. It
is the default base URL for that reason.

That is bigger than the question it was run for. Per-entry cast joined on
id_mal, with a language on every voice actor, is what ANN's title match
only ever delivered for 31.8% of the catalog - so this is a candidate for
the whole non-English language walk, not only for the 37 titles the
Dubbing Wiki census left unanswered.

Caveat worth keeping: Tenrai is a community service a month into this
role. Fine to measure against; a catalog that depends on it inherits its
uptime.

The history, kept because it cost two investigations: Jikan's
public API was discontinued on 1 October 2026 (jikan-rest issue #613,
announced that June). A run of --check hangs for four minutes and then
reports failure, from a GitHub runner and from the deployment host alike,
because there is nothing there - not because either host is blocked. The
earlier note in this repository, "api.jikan.moe refuses GitHub's runners
(ConnectError Errno 101)", described the symptom of a shutdown as if it
were a network policy, and sent one investigation after container DNS and
egress rules.

That is the second time in this project that a recorded transport error
masked an endpoint that no longer exists; the first was MAL's own user
list endpoints, discontinued in 2022 and chased as a connectivity
problem. The lesson both times: when a host stops answering, check
whether the service still exists before checking the network.

The MAL-shaped question this module asks is still a good one. It just
cannot be answered through Jikan any more, and the official MAL API has
no cast resource at all (measured: fields=characters,
characters{voice_actors}, voice_actors and cast all return 200 with the
default payload and no such key). Kitsu is the live candidate -
character-voices carries a locale code and mappings joins by AniList id,
both measured in pipeline.probe_kitsu.

That last point is the question, so the sample is stratified by obscurity
and reported per band. Popularity is a MEASUREMENT axis here and never a
ranking input - the catalog's rule is that it must not enter scoring, and
nothing here scores anything.

Three things it is careful to keep apart, because a probe that reports its
own failure as a finding has already happened four times in this
investigation: a title MAL has no entry for (404), a request that failed,
and a title MAL answers for with no English cast. Only the third is evidence
about dubs.

    docker exec mangabrain-worker-1 python -m pipeline.probe_dub_mal --check
    docker exec mangabrain-worker-1 python -m pipeline.probe_dub_mal
"""

import argparse
import pathlib
import random
from collections import Counter

import httpx

from api.config import settings
from pipeline.backfill_dub_ann import NATIVE_LANGS
from pipeline.client import RateLimitedClient
from pipeline.dub_language_codes import code_for
from pipeline.stats import wilson_interval

# Jikan's own public API is gone (see the note above). Tenrai is the
# successor named by the projects that migrated off it, and is reported to
# be schema-compatible with Jikan v4 - the same /anime/{id}/characters
# shape with voice_actors[].language - so a base URL is all that should
# need to change. Reported, not verified: --base-url exists so the claim
# can be tested with one request instead of believed.
JIKAN_BASE = "https://api.jikan.moe/v4"
TENRAI_BASE = "https://api.tenrai.org/v1"

# Shorthands, so a dispatch can say "tenrai" rather than paste a URL.
KNOWN_BASES = {
    "jikan": JIKAN_BASE,
    "tenrai": TENRAI_BASE,
}

# Jikan allows 3 requests/second but also 60/minute, and the per-minute limit
# is the binding one: 1.1s between requests stays under it with margin. A
# faster floor just earns 429s and the backoff that follows.
DEFAULT_INTERVAL = 1.1

# Obscurity bands, loosest first. The named edges are arbitrary; what matters
# is that each band holds enough of the gap set to measure.
BANDS: tuple[tuple[str, int | None, int | None], ...] = (
    ("top (100k+)", 100000, None),
    ("20k-100k", 20000, 100000),
    ("5k-20k", 5000, 20000),
    ("1k-5k", 1000, 5000),
    ("100-1k", 100, 1000),
    ("under 100", 0, 100),
    ("unknown", None, None),
)

# The gap set: anime with no English dub from any source, which is what the
# dub filter would refuse to serve. Matches api/filters.py's English clause.
GAP_SQL = """
    SELECT m.id, m.id_mal, m.title_romaji, m.title_english, m.popularity,
           m.country_of_origin, m.cast_character_count,
           m.dub_languages_source, m.dub_languages, m.measured_dub_languages
    FROM media m
    WHERE m.media_type = 'ANIME'
      AND m.id_mal IS NOT NULL
      AND NOT (m.dub_languages @> ARRAY['en']::text[]
               OR m.measured_dub_languages @> ARRAY['en']::text[]
               OR m.english_va_count > 0
               OR m.english_dub_inferred)
"""


# The same row shape as GAP_SQL for an explicit list of ids, without the
# gap predicate: --ids is for checking titles someone has already decided
# are interesting, and filtering them by our own gap again would silently
# drop any that a later sync has since answered - which is information,
# not noise.
BY_ID_SQL = """
    SELECT m.id, m.id_mal, m.title_romaji, m.title_english, m.popularity,
           m.country_of_origin, m.cast_character_count,
           m.dub_languages_source, m.dub_languages, m.measured_dub_languages,
           (m.dub_languages @> ARRAY['en']::text[]
            OR m.measured_dub_languages @> ARRAY['en']::text[]
            OR m.english_va_count > 0
            OR m.english_dub_inferred) AS english_known
    FROM media m
    WHERE m.id = ANY(%(ids)s)
"""


def _connect():
    """Open a catalog connection.

    Imported here rather than at module scope so --check, which asks one
    question of Jikan and touches no database, runs anywhere httpx is
    installed. A CI run died at `import psycopg` before reaching the
    reachability test it existed to perform, which is a silly way to fail
    a probe whose entire job is to distinguish "the host refused" from
    everything else.
    """
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(settings.database_url, row_factory=dict_row)


ID_MAL_QUERY = """
query ($ids: [Int]) {
  Page(page: 1, perPage: 50) {
    media(id_in: $ids, type: ANIME) { id idMal title { romaji english } }
  }
}
"""


def resolve_id_mal(client: RateLimitedClient, ids: list[int]) -> dict[int, dict]:
    """AniList id -> {id_mal, title}, asked of AniList rather than the catalog.

    The catalog knows this already, but only the deployment host has the
    catalog, and that is the single reason this check could not run in CI.
    AniList serves idMal on the media it owns, 50 per page, so the mapping
    costs one request per fifty titles and the probe runs anywhere.

    Not a replacement for the database path: that one also reports whether
    our own sources have since answered for a title, which AniList cannot
    know.
    """
    from pipeline.sync_anilist import fetch_page

    out: dict[int, dict] = {}
    for start in range(0, len(ids), 50):
        chunk = ids[start : start + 50]
        page = fetch_page(client, ID_MAL_QUERY, {"ids": chunk})
        for media in page.get("media") or []:
            titles = media.get("title") or {}
            out[media["id"]] = {
                "id_mal": media.get("idMal"),
                "title": titles.get("english") or titles.get("romaji") or "",
            }
    return out


def run_for_ids_via_anilist(client: RateLimitedClient, ids: list[int], base_url: str) -> None:
    """The same question without a catalog: map through AniList, then ask.

    Reports no "already known" column, because that fact lives in our
    database and this path does not have one. Saying so beats inventing a
    value for it.
    """
    print(f"parsed {len(ids)} ids")
    _, status = mal_languages(client, 1, base_url)
    if status != "ok":
        print(f"\nthe cast API did not answer for a known-good title (status={status});")
        print("nothing below would mean anything, so stopping.")
        return

    mapping = resolve_id_mal(client, ids)
    missing = [i for i in ids if i not in mapping]
    if missing:
        print(f"AniList returned nothing for: {missing}")

    english = answered = absent = failed = no_mal_id = 0
    for media_id in ids:
        entry = mapping.get(media_id)
        if entry is None:
            continue
        title = entry["title"][:46]
        id_mal = entry["id_mal"]
        if not id_mal:
            no_mal_id += 1
            print(f"  NO MAL #{media_id:>7}  {title}  (AniList stores no idMal)")
            continue
        langs, status = mal_languages(client, id_mal, base_url)
        if status == "absent":
            absent += 1
            print(f"  absent #{media_id:>7}  {title}  (no entry under mal id {id_mal})")
            continue
        if status == "failed":
            failed += 1
            print(f"  FAILED #{media_id:>7}  {title}  (request failed)")
            continue
        answered += 1
        codes, _unknown = dub_codes(langs, None)
        if "en" in codes:
            english += 1
            print(f"  DUB    #{media_id:>7}  {title}  languages={sorted(codes)}")
        else:
            print(f"  no en  #{media_id:>7}  {title}  languages={sorted(codes) or 'none'}")

    print(f"\nasked about {answered} of {len(ids)} ids")
    print(f"  English cast credited: {english}")
    print(f"  answered, no English cast: {answered - english}")
    print(f"  no entry: {absent}   no idMal on AniList: {no_mal_id}   failed: {failed}")
    print(
        "\nNo rate is reported: these ids were hand-picked, so a percentage"
        "\nover them would describe the picking. No 'already known' column"
        "\neither - that lives in the catalog, which this path does not have."
    )


def parse_ids(raw: str) -> list[int]:
    """Comma or whitespace separated AniList ids."""
    return [int(part) for part in raw.replace(",", " ").split() if part.strip()]


def run_for_ids(client: RateLimitedClient, ids: list[int], base_url: str = TENRAI_BASE) -> None:
    """Ask MAL about specific titles, one line of verdict each.

    No sampling and no bands: this answers "can MAL fill these particular
    holes", which is a different question from "what is MAL's rate", and
    reporting a rate over a hand-picked list would be a statistic about
    the picking.
    """
    print(f"parsed {len(ids)} ids")
    # Reachability first, in one request. Without this a blocked host
    # produces one "request failed" line per title and buries the only
    # fact that matters - and this investigation has already spent four
    # probes reporting their own transport failure as a finding.
    _, status = mal_languages(client, 1, base_url)
    if status != "ok":
        print(
            f"\nJikan did not answer for a known-good title (status={status})."
            "\nNothing below would mean anything, so stopping. The dub data"
            "\nlives on api.jikan.moe/v4/anime/{id}/characters - note that is"
            "\nNOT the official MAL API this project now uses for lists, and"
            "\nthe official API has no cast resource to fall back on."
        )
        return

    with _connect() as conn:
        rows = conn.execute(BY_ID_SQL, {"ids": ids}).fetchall()
    found = {row["id"] for row in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        print(f"not in the catalog: {missing}")

    english = answered = absent = failed = no_mal_id = already = 0
    for row in sorted(rows, key=lambda r: ids.index(r["id"])):
        title = (row["title_english"] or row["title_romaji"] or "")[:46]
        if row["english_known"]:
            # Not an error: a sync may have answered it since the list was
            # drawn up, and saying so is more useful than skipping it.
            already += 1
            print(f"  known  #{row['id']:>7}  {title}  (we already have an English dub)")
            continue
        if row["id_mal"] is None:
            no_mal_id += 1
            print(f"  NO MAL #{row['id']:>7}  {title}  (no id_mal stored; MAL cannot be asked)")
            continue
        langs, status = mal_languages(client, row["id_mal"], base_url)
        if status == "absent":
            absent += 1
            print(f"  absent #{row['id']:>7}  {title}  (no MAL entry under id {row['id_mal']})")
            continue
        if status == "failed":
            failed += 1
            print(f"  FAILED #{row['id']:>7}  {title}  (request failed)")
            continue
        answered += 1
        codes, unknown_names = dub_codes(langs, row["country_of_origin"])
        if "en" in codes:
            english += 1
            print(f"  DUB    #{row['id']:>7}  {title}  languages={sorted(codes)}")
        else:
            shown = sorted(codes) or "none"
            print(f"  no en  #{row['id']:>7}  {title}  languages={shown}")
        for bad in unknown_names:
            print(f"           unmapped language name: {bad}")

    print(f"\nasked MAL about {answered} of {len(ids)} ids")
    print(f"  English cast credited: {english}")
    print(f"  answered, no English cast: {answered - english}")
    print(f"  already answered by our own sources: {already}")
    print(f"  no MAL entry: {absent}   no id_mal stored: {no_mal_id}   request failed: {failed}")
    print(
        "\nNo rate is reported: these ids were hand-picked, so a percentage"
        "\nover them would describe the picking rather than MAL."
    )


def band_of(popularity: int | None) -> str:
    if popularity is None:
        return "unknown"
    for name, low, high in BANDS:
        if low is None:
            continue
        if popularity >= low and (high is None or popularity < high):
            return name
    return "unknown"


def mal_languages(
    client: RateLimitedClient, id_mal: int, base_url: str = TENRAI_BASE
) -> tuple[set[str], str]:
    """(languages MAL credits, status). Status is ok, absent, or failed.

    A 404 means MAL has no entry under that id, which is a real answer about
    coverage. Any other failure is the probe's problem and must never be
    counted as "no English" - a swallowed HTTP 400 once printed as
    "0/200 (0.0%)" in this very investigation.
    """
    try:
        data = client.request("GET", f"{base_url}/anime/{id_mal}/characters")
    except httpx.HTTPStatusError as exc:
        return set(), "absent" if exc.response.status_code == 404 else "failed"
    except httpx.HTTPError:
        return set(), "failed"
    langs: set[str] = set()
    for entry in data.get("data") or []:
        for actor in entry.get("voice_actors") or []:
            name = (actor.get("language") or "").strip()
            if name:
                langs.add(name)
    return langs, "ok"


def dub_codes(langs: set[str], country: str | None) -> tuple[set[str], set[str]]:
    """Language codes minus the title's own, plus any name with no code.

    A Japanese cast on a Japanese show is the original, not a dub - the same
    rule the AniList and ANN passes use, driven by country_of_origin because
    a Japanese cast on a Chinese donghua genuinely IS a dub. An unmapped
    language name is reported rather than dropped, so a vocabulary gap shows
    up as a gap instead of as an absence of dubs.
    """
    native = NATIVE_LANGS.get((country or "").upper(), set())
    codes: set[str] = set()
    unknown: set[str] = set()
    for name in langs:
        code, known = code_for(name)
        if not known:
            unknown.add(name)
        if code not in native:
            codes.add(code)
    return codes, unknown


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure MAL as a dub source")
    parser.add_argument("--check", action="store_true", help="one request, test reachability")
    parser.add_argument(
        "--base-url",
        # Tenrai by default, because the alternative is defaulting to a host
        # that was switched off on 1 Oct 2026 and hangs for four minutes
        # before saying so. "jikan" still works as a shorthand, for anyone
        # pointing this at a self-hosted jikan-rest.
        default="tenrai",
        help="tenrai (default), jikan, or a full base URL",
    )
    parser.add_argument("--per-band", type=int, default=40, help="titles sampled per band")
    parser.add_argument("--seed", type=int, default=20261104)
    parser.add_argument("--min-interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--cache-dir", default="", help="on-disk cache, so a rerun is free")
    parser.add_argument(
        "--ids",
        default="",
        help="check these AniList ids instead of sampling (comma or space separated)",
    )
    parser.add_argument(
        "--via-anilist",
        action="store_true",
        help="map ids through AniList instead of the catalog, so this runs without a database",
    )
    parser.add_argument(
        "--ids-file",
        default="",
        help="read the ids from a file instead, which a shell cannot wrap mid-number",
    )
    args = parser.parse_args()

    base_url = KNOWN_BASES.get(args.base_url, args.base_url).rstrip("/")
    client = RateLimitedClient(min_interval=args.min_interval, cache_dir=args.cache_dir)
    if args.check:
        # Settle the stale blocker before spending a sample on it.
        langs, status = mal_languages(client, 1, base_url)
        client.close()
        print(f"base: {base_url}")
        print(f"MAL anime 1 (Cowboy Bebop): status={status} languages={sorted(langs)}")
        if status == "ok" and "English" in langs:
            print("Reachable, and it credits an English cast. Run the probe.")
        elif status == "ok":
            print("reachable, but no English cast on a certainly-dubbed title: investigate")
            print("(a schema change would look exactly like this: check voice_actors[].language)")
        else:
            print(
                "Jikan did not answer. Not a blocker to work around: the public"
                "\nAPI was discontinued on 1 October 2026 (jikan-rest issue"
                "\n#613). It will not answer from any host. Cast data needs a"
                "\ndifferent source - Kitsu carries locale codes on"
                "\ncharacter-voices and joins by id through mappings."
            )
        return

    if args.ids or args.ids_file:
        raw = args.ids
        if args.ids_file:
            raw = f"{raw} {pathlib.Path(args.ids_file).read_text()}"
        ids = parse_ids(raw)
        if args.via_anilist:
            run_for_ids_via_anilist(client, ids, base_url)
        else:
            run_for_ids(client, ids, base_url)
        client.close()
        return

    rng = random.Random(args.seed)
    with _connect() as conn:
        rows = conn.execute(GAP_SQL).fetchall()
    print(f"{len(rows)} anime in the gap set with an id_mal (no English dub from any source)")

    grouped: dict[str, list[dict]] = {name: [] for name, _lo, _hi in BANDS}
    for row in rows:
        grouped[band_of(row["popularity"])].append(row)
    print("\ngap set by obscurity band:")
    for name, _lo, _hi in BANDS:
        print(f"   {name:<14} {len(grouped[name]):>6}")

    print(
        f"\nsampling up to {args.per_band} per band at {args.min_interval}s between"
        " requests (Jikan allows 60/min)\n"
    )
    languages: Counter[str] = Counter()
    unmapped: Counter[str] = Counter()
    net_new = 0
    totals = {"english": 0, "answered": 0, "absent": 0, "failed": 0}
    per_band: dict[str, tuple[int, int, int, int]] = {}

    for name, _lo, _hi in BANDS:
        pool = grouped[name]
        if not pool:
            continue
        chosen = pool if len(pool) <= args.per_band else rng.sample(pool, args.per_band)
        english = answered = absent = failed = 0
        for row in chosen:
            langs, status = mal_languages(client, row["id_mal"], base_url)
            if status == "absent":
                absent += 1
                continue
            if status == "failed":
                failed += 1
                continue
            answered += 1
            codes, unknown_names = dub_codes(langs, row["country_of_origin"])
            for code in codes:
                languages[code] += 1
            for bad in unknown_names:
                unmapped[bad] += 1
            if "en" in codes:
                english += 1
                # ANN having already answered "no dubs" for this row makes a
                # MAL English cast a genuine disagreement, not just a gap.
                if row["dub_languages_source"] != "ann":
                    net_new += 1
                title = (row["title_english"] or row["title_romaji"] or "")[:44]
                print(
                    f"   DUB  #{row['id']:>7} mal={row['id_mal']:>7}"
                    f" pop={row['popularity'] or 0:>7}  {title}"
                )
        per_band[name] = (english, answered, absent, failed)
        for key, value in (
            ("english", english),
            ("answered", answered),
            ("absent", absent),
            ("failed", failed),
        ):
            totals[key] += value
    client.close()

    print("\n" + "=" * 74)
    print("English dub rate among titles with NO English dub on record")
    print("=" * 74)
    print(f"   {'band':<14} {'english/answered':<18} {'rate':<16} absent  failed")
    for name, _lo, _hi in BANDS:
        if name not in per_band:
            continue
        english, answered, absent, failed = per_band[name]
        if answered:
            low, high = wilson_interval(english, answered)
            rate = f"{100 * low:.0f}-{100 * high:.0f}%"
        else:
            rate = "unmeasured"
        print(f"   {name:<14} {f'{english}/{answered}':<18} {rate:<16} {absent:>6}  {failed:>6}")
    if totals["answered"]:
        low, high = wilson_interval(totals["english"], totals["answered"])
        gap_with_mal = sum(len(grouped[n]) for n, _lo, _hi in BANDS)
        print(
            f"\n   overall {totals['english']}/{totals['answered']}"
            f" = {100 * low:.0f}-{100 * high:.0f}%"
            f"  -> {int(low * gap_with_mal)}-{int(high * gap_with_mal)}"
            f" of {gap_with_mal} gap titles"
        )
        print(f"   {net_new} of the English hits are ones ANN never answered for")
    print(
        f"\n   {totals['absent']} titles have no MAL entry under their stored id_mal;"
        f"\n   {totals['failed']} requests failed outright. Neither is evidence about dubs."
    )
    if languages:
        print("\n   every dub language MAL credited in the sample:")
        for code, count in languages.most_common(20):
            print(f"     {code:<8} {count:>5}")
    if unmapped:
        print("\n   language names with no code mapping (vocabulary gap, not absence):")
        for name, count in unmapped.most_common(10):
            print(f"     {name:<28} {count:>5}")
    print(
        "\n   The decision: MAL is worth building if the rate holds up in the"
        "\n   obscure bands. A source that only confirms dubs at the top of the"
        "\n   catalog adds nothing - AniList already covers that end, and it is"
        "\n   the obscure end this project exists to serve."
    )


if __name__ == "__main__":
    main()
