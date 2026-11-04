"""Near-miss search and the composed variant, both from one real case.

The Amazing 3 (1965) came back unmatched against all five names AniList
holds, which left two very different situations indistinguishable: ANN has
no entry, or ANN files it under a name none of those five is. ANN's sorted
form of that title would be 'Amazing Three, The' - which neither the article
swap nor the number spelling produces on its own, so the composition is its
own family and the search has to unify '3' with 'three' or it misses exactly
the titles the variants exist to fix.
"""

from pipeline.ann import AnnEntry
from pipeline.title_variants import (
    near_misses,
    tokens,
    variant_both,
    variant_names,
)

CANDIDATES = ["The Amazing 3", "Wonder 3", "W3", "Wondering Three"]


def entry(name: str, kind: str = "TV", year: int | None = 1965, ann_id: str = "1") -> AnnEntry:
    return AnnEntry(ann_id=ann_id, name=name, kind=kind, year=year)


def test_digits_and_number_words_collapse_to_one_token():
    assert tokens("The Amazing 3") == tokens("The Amazing Three")
    assert "three" in tokens("The Amazing 3")


def test_articles_and_single_characters_carry_no_identity():
    """Without this, every English title looks a little like every other."""
    assert "the" not in tokens("The Amazing 3")
    assert tokens("A of the and") == set()


def test_composed_variant_reaches_anns_sorted_spelled_form():
    """Neither single transform produces 'Amazing Three, The'."""
    assert "Amazing Three, The" in variant_both("The Amazing 3")


def test_composed_variant_is_not_already_covered():
    """Pinning why article_and_number is a separate family, not redundant."""
    singles = set(variant_names("article_swap", ["The Amazing 3"])) | set(
        variant_names("number_words", ["The Amazing 3"])
    )
    assert "Amazing Three, The" not in singles


def test_near_miss_finds_the_entry_under_anns_own_name():
    hits = near_misses(
        [entry("Amazing Three, The"), entry("Unrelated Show", ann_id="2")], CANDIDATES
    )
    assert hits, "the sorted, spelled form should be found"
    assert hits[0][1].name == "Amazing Three, The"


def test_near_miss_stays_quiet_when_nothing_is_related():
    hits = near_misses([entry("Cowboy Bebop"), entry("Lupin III", ann_id="2")], CANDIDATES)
    assert hits == []


def test_one_short_shared_token_is_not_a_near_miss():
    """Noise guard: a single weak token in common is not evidence.

    Reporting those would bury the real entry under dozens of titles that
    merely share a number, which is the failure mode of a looser search -
    and an explainer nobody trusts gets ignored.
    """
    assert near_misses([entry("Apollo 3")], ["The Amazing 3"]) == []


def test_near_miss_reports_contradicted_entries_too():
    """It is reporting only, and must not quietly apply the guards.

    The caller prints whether year and format agree and decides. If this
    filtered contradicted entries out, a title ANN holds under a wrong year
    would read as 'ANN has no entry', which is the exact confusion the
    search was added to end.
    """
    hits = near_misses([entry("Amazing Three, The", kind="OAV", year=1999)], CANDIDATES)
    assert hits and hits[0][1].year == 1999


def test_near_miss_is_ordered_and_capped():
    pool = [entry(f"Amazing Three Part {i}", ann_id=str(i)) for i in range(20)]
    hits = near_misses(pool, CANDIDATES, limit=5)
    assert len(hits) == 5
    assert [score for score, _e in hits] == sorted((s for s, _e in hits), reverse=True)


def test_empty_candidates_find_nothing():
    assert near_misses([entry("Amazing Three, The")], []) == []
    assert near_misses([], CANDIDATES) == []
