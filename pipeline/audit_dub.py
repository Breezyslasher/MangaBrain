"""Where the missing dubs are, catalog-wide, and which fix would recover most.

explain_dub answers "why this title". That does not scale: The Amazing 3 and
Discode were found by eye, and nobody is going to eye 15,000 rows. This
answers the general question instead - it classifies the whole gap by cause,
then measures, per cause, how many titles a fix would actually recover.

The ANN pass already prints a verdict histogram, and that histogram is not
the same question. It counts verdicts over every row ANN has not answered
for, including rows that already have an English dub from AniList, where a
matcher fix recovers nothing. And a verdict count says how many titles are
in a bucket, never how many of them ANN actually credits an English cast
for - which is the number that decides whether a class is worth the
false-positive risk of loosening a guard. 1,228 format rejections are worth
attacking if ANN holds dubs for 900 of them and not if it holds dubs for 12.

So this restricts to titles with NO English dub from any source - the
complaint set - and measures recovery by sampling ANN's casts per bucket,
reporting a Wilson interval rather than a point estimate.

Stage D is the one that found this class of bug in the first place: it adds
candidate name variants and reports how many unmatched titles would gain a
CORROBORATED unique hit, without changing the matcher. base_form already
strips a trailing one- or two-digit number as a season marker, so
'The Amazing 3' reduces to 'The Amazing' - for a title whose number is part
of its name (Patlabor 2, Mobile Suit Gundam 00) that is wrong, and a variant
family measured here says how much it costs.

Read-only. It writes nothing, ever.

    docker exec mangabrain-worker-1 python -m pipeline.audit_dub
    docker exec mangabrain-worker-1 python -m pipeline.audit_dub --no-network
"""

import argparse
import random
import re
from collections import Counter, defaultdict

import httpx
import psycopg
from psycopg.rows import dict_row

from api.config import settings
from pipeline.ann import (
    UA,
    AnnEntry,
    build_index,
    fetch_anime_index,
    fetch_casts,
    match,
    rejected_against,
)
from pipeline.backfill_dub_ann import candidate_titles, dub_languages_for
from pipeline.stats import wilson_interval

ALL_ANIME_SQL = """
    SELECT m.id, m.title_romaji, m.title_english, m.title_native, m.synonyms,
           m.start_year, m.format, m.country_of_origin,
           m.english_va_count, m.cast_character_count, m.english_dub_inferred,
           m.dub_languages, m.dub_languages_source, m.ann_id,
           m.measured_dub_languages,
           m.measured_dub_checked_at IS NOT NULL AS lang_checked,
           m.dub_checked_at IS NOT NULL AS en_checked
    FROM media m
    WHERE m.media_type = 'ANIME'
"""

# Buckets a fix could move, in the order a fix should be considered.
FIXABLE = ("rejected_year", "rejected_format", "ambiguous_exact", "ambiguous_base", "base")


def has_english_dub(row: dict) -> bool:
    """Exactly what the dub filter serves, so the gap set is the complaint set.

    Duplicating the filter's four-way test in different words here would let
    the audit measure a gap the UI does not have (or miss one it does), which
    is the one thing an audit must not do.
    """
    return bool(
        "en" in (row["dub_languages"] or [])
        or "en" in (row["measured_dub_languages"] or [])
        or (row["english_va_count"] or 0) > 0
        or row["english_dub_inferred"]
    )


def dub_source(row: dict) -> str:
    """Which source answered, for a covered row. Measured beats inferred."""
    if (row["english_va_count"] or 0) > 0:
        return "anilist_english_cast"
    if "en" in (row["measured_dub_languages"] or []):
        return "anilist_language_walk"
    if "en" in (row["dub_languages"] or []):
        return "ann"
    return "franchise_inferred"


def gap_cause(row: dict) -> str:
    """Why a row with no English dub has none, from stored columns alone.

    Pending states are tested first and deliberately: a row no pass has
    visited is not evidence of anything, and calling it a source limitation
    would send the next investigation after a gap that does not exist. The
    whole dub design treats missing data as not-evidence - the filter is
    positive-only, english_va_count = 0 is never served as sub-only - and an
    audit that broke that rule would be worse than no audit.
    """
    if not row["en_checked"] and not row["lang_checked"]:
        return "pending_anilist"
    if (row["cast_character_count"] or 0) == 0:
        return "anilist_no_cast"
    return "anilist_cast_without_english"


def ann_state(row: dict) -> str:
    if row["dub_languages_source"] == "ann":
        return "ann_answered_no_english"
    return "ann_not_answered"


# ---------------------------------------------------------------- variants

ARTICLES = ("The", "A", "An")
NUMBER_WORDS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
    "10": "ten",
    "11": "eleven",
    "12": "twelve",
    "13": "thirteen",
    "14": "fourteen",
    "15": "fifteen",
    "16": "sixteen",
    "17": "seventeen",
    "18": "eighteen",
    "19": "nineteen",
    "20": "twenty",
}
WORD_NUMBERS = {word: digit for digit, word in NUMBER_WORDS.items()}
DIGIT_RE = re.compile(r"\b(\d{1,2})\b")
WORD_RE = re.compile(r"\b(" + "|".join(WORD_NUMBERS) + r")\b", re.I)


def variant_article(title: str) -> list[str]:
    """'The Amazing 3' <-> 'Amazing 3, The'. ANN files some titles sorted."""
    out = []
    for article in ARTICLES:
        prefix = f"{article} "
        if title.lower().startswith(prefix.lower()):
            out.append(f"{title[len(prefix) :]}, {article}")
        suffix = f", {article}"
        if title.lower().endswith(suffix.lower()):
            out.append(f"{article} {title[: -len(suffix)]}")
    return out


def variant_numbers(title: str) -> list[str]:
    """'The Amazing 3' <-> 'The Amazing Three'.

    Both directions, because which form a source picked is not predictable:
    AniList holds 'The Amazing 3' while ANN may hold the spelled form, and
    nothing says it is not the other way round for the next title.
    """
    out = []
    spelled = DIGIT_RE.sub(lambda m: NUMBER_WORDS.get(m.group(1), m.group(1)), title)
    if spelled != title:
        out.append(spelled)
    digits = WORD_RE.sub(lambda m: WORD_NUMBERS[m.group(1).lower()], title)
    if digits != title:
        out.append(digits)
    return out


def variant_subtitle(title: str) -> list[str]:
    """Drop a trailing subtitle: sources disagree on whether to carry it."""
    for sep in (": ", " - ", " ~ "):
        if sep in title:
            head = title.split(sep, 1)[0].strip()
            if len(head) >= 4:
                return [head]
    return []


VARIANTS = {
    "article_swap": variant_article,
    "number_words": variant_numbers,
    "drop_subtitle": variant_subtitle,
}


def variant_names(row: dict, family: str) -> list[str]:
    """Extra candidate names from one variant family, none already known."""
    known = {n.strip().lower() for n in candidate_titles(row)}
    out: list[str] = []
    for name in candidate_titles(row):
        for extra in VARIANTS[family](name):
            key = extra.strip().lower()
            if key and key not in known:
                known.add(key)
                out.append(extra)
    return out


# ---------------------------------------------------------------- reporting


def rule(text: str) -> None:
    print()
    print("=" * 74)
    print(text)
    print("=" * 74)


def bar(count: int, total: int) -> str:
    share = 100.0 * count / total if total else 0.0
    return f"{count:>6} ({share:>5.1f}%)"


def sample_english_rate(
    client: httpx.Client,
    pairs: list[tuple[int, AnnEntry, str | None]],
    sample: int,
    rng: random.Random,
) -> tuple[int, int, int]:
    """(english, answered, no_data) over a random sample of (row, entry).

    A cast ANN has not filled is counted as no_data, never as no_english.
    Four measurements in this investigation reported a probe's own failure as
    a finding - a swallowed HTTP 400 printed as '0.0%', 404s counted as
    rejections - and this is the same shape, so the two are kept apart and
    both are printed.
    """
    chosen = pairs if len(pairs) <= sample else rng.sample(pairs, sample)
    english = answered = no_data = 0
    for start in range(0, len(chosen), 20):
        batch = chosen[start : start + 20]
        try:
            casts = fetch_casts(client, [entry.ann_id for _mid, entry, _c in batch])
        except httpx.HTTPError as exc:
            print(f"     cast fetch failed ({type(exc).__name__}: {exc}); batch skipped")
            no_data += len(batch)
            continue
        for _media_id, entry, country in batch:
            if entry.ann_id not in casts:
                no_data += 1
                continue
            _en, langs = casts[entry.ann_id]
            if not langs:
                no_data += 1
                continue
            answered += 1
            if "en" in dub_languages_for(langs, country):
                english += 1
    return english, answered, no_data


def estimate(label: str, english: int, answered: int, no_data: int, bucket: int) -> float:
    """Print a sampled rate as a range, and extrapolate. Returns the low end."""
    if answered == 0:
        print(f"   {label:<22} no ANN cast data in the sample ({no_data} empty); unmeasured")
        return 0.0
    low, high = wilson_interval(english, answered)
    print(
        f"   {label:<22} {english}/{answered} credit English"
        f"  = {100 * low:.0f}-{100 * high:.0f}%"
        f"  -> {int(low * bucket)}-{int(high * bucket)} of {bucket} titles"
        + (f"  ({no_data} no cast data)" if no_data else "")
    )
    return low


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit the catalog's dub gap by cause")
    parser.add_argument("--sample", type=int, default=120, help="titles sampled per bucket")
    parser.add_argument("--seed", type=int, default=20261104)
    parser.add_argument("--no-network", action="store_true", help="stage A only, from the DB")
    parser.add_argument("--variants-only", action="store_true", help="skip to stage D")
    args = parser.parse_args()
    rng = random.Random(args.seed)

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        rows = conn.execute(ALL_ANIME_SQL).fetchall()
    total = len(rows)
    covered = [r for r in rows if has_english_dub(r)]
    gap = [r for r in rows if not has_english_dub(r)]

    rule("A. coverage, and the shape of what is missing (no network)")
    print(f"   {total} anime; {bar(len(covered), total)} have an English dub")
    print("\n   covered, by the source that answered:")
    for source, count in Counter(dub_source(r) for r in covered).most_common():
        print(f"     {source:<24} {bar(count, len(covered))}")
    print(f"\n   the gap: {len(gap)} titles with no English dub from any source")
    for cause, count in Counter(gap_cause(r) for r in gap).most_common():
        print(f"     {cause:<30} {bar(count, len(gap))}")
    print("\n   and what ANN has said about those same rows:")
    for state, count in Counter(ann_state(r) for r in gap).most_common():
        print(f"     {state:<30} {bar(count, len(gap))}")
    print(
        "\n   pending_anilist is not a gap - no pass has visited those rows."
        "\n   anilist_no_cast cannot be fixed from AniList at any price."
        "\n   The rest is where a matcher fix can act, measured below."
    )
    if args.no_network:
        return

    with httpx.Client(timeout=90.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
        print("\nfetching ANN's anime index (22 requests, once)...", flush=True)
        entries = fetch_anime_index(client)
        index = build_index(entries)
        print(f"{len(entries)} ANN entries, {len(index)} keys", flush=True)

        # Only rows ANN has not already answered for: a row where ANN said
        # "no dubs" is not waiting on the matcher.
        pending = [r for r in gap if r["dub_languages_source"] != "ann"]
        verdicts: Counter[str] = Counter()
        buckets: dict[str, list[tuple[int, AnnEntry, str | None]]] = defaultdict(list)
        unmatched: list[dict] = []
        pairs: Counter[tuple[str, str]] = Counter()

        if not args.variants_only:
            rule("B. what the matcher does with the gap (not with the whole catalog)")
            for row in pending:
                names = candidate_titles(row)
                verdict, entry = match(index, names, year=row["start_year"], fmt=row["format"])
                verdicts[verdict] += 1
                if verdict == "unmatched":
                    unmatched.append(row)
                    continue
                if entry is None and verdict.startswith("rejected_"):
                    entry = rejected_against(
                        index, names, year=row["start_year"], fmt=row["format"]
                    )
                    if entry is not None:
                        pairs[(row["format"] or "?", entry.kind or "?")] += 1
                if entry is not None:
                    buckets[verdict].append((row["id"], entry, row["country_of_origin"]))
            print(f"   {len(pending)} gap titles ANN has not answered for:")
            for verdict, count in verdicts.most_common():
                print(f"     {verdict:<22} {bar(count, len(pending))}")
            if pairs:
                print("\n   format rejections, (AniList format -> ANN type):")
                for (fmt, kind), count in pairs.most_common(12):
                    print(f"     {fmt:<12} -> {kind:<16} {count:>6}")

            rule("C. how many English dubs each fixable class would actually recover")
            print("   sampled from ANN's casts; a range, not a point estimate\n")
            ranked: list[tuple[float, str, int]] = []
            for verdict in FIXABLE:
                pool = buckets.get(verdict) or []
                if not pool:
                    continue
                english, answered, no_data = sample_english_rate(client, pool, args.sample, rng)
                low = estimate(verdict, english, answered, no_data, len(pool))
                ranked.append((low * len(pool), verdict, len(pool)))
            if ranked:
                print("\n   fix in this order (titles recovered, conservative):")
                for value, verdict, size in sorted(ranked, reverse=True):
                    print(f"     {verdict:<22} >= {int(value):>5} of {size}")
            else:
                print("   no fixable bucket had any entries")
        else:
            unmatched = [
                r
                for r in pending
                if match(index, candidate_titles(r), year=r["start_year"], fmt=r["format"])[0]
                == "unmatched"
            ]

        rule("D. name variants: would a looser key find these, and would it be right?")
        print(
            f"   {len(unmatched)} unmatched gap titles. Each family below is MEASURED,"
            "\n   not applied, and every hit still has to pass the year and format"
            "\n   guard - so a count here is recoverable titles, not name collisions.\n"
        )
        for family in VARIANTS:
            hits: list[tuple[int, AnnEntry, str | None]] = []
            blocked = 0
            for row in unmatched:
                extra = variant_names(row, family)
                if not extra:
                    continue
                verdict, entry = match(index, extra, year=row["start_year"], fmt=row["format"])
                if verdict == "exact" and entry is not None:
                    hits.append((row["id"], entry, row["country_of_origin"]))
                elif verdict.startswith("rejected_") or verdict.startswith("ambiguous_"):
                    blocked += 1
            print(f"   {family}: {len(hits)} corroborated unique hits, {blocked} rejected")
            if hits:
                english, answered, no_data = sample_english_rate(client, hits, args.sample, rng)
                estimate(f"  -> {family}", english, answered, no_data, len(hits))
                for media_id, entry, _c in hits[:5]:
                    print(f"        #{media_id} -> ann {entry.ann_id} {entry.name!r}")

        rule("what to do with this")
        print(
            "   A class is worth a fix when its recovered-titles range clears the"
            "\n   false positives a looser guard would admit. The guards exist"
            "\n   because a 2-character Suntory advert was once stored as the dub"
            "\n   for 'your name.', so loosening one is a trade, never a cleanup -"
            "\n   and section C is the side of that trade that was never measured."
        )


if __name__ == "__main__":
    main()
