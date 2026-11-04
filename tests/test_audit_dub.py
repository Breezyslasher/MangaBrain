"""The catalog-wide dub audit must measure the gap the UI actually has.

An audit that measures a slightly different gap than the dub filter serves
is worse than no audit: it would send work at titles users can already find,
or miss ones they cannot. So the predicate is pinned against api/filters.py
itself, and the bucketing is pinned against the one rule the whole dub
design rests on - missing data is not evidence.
"""

import random
import re
from pathlib import Path

import pytest

from pipeline.ann import AnnEntry
from pipeline.audit_dub import (
    VARIANTS,
    dub_source,
    gap_cause,
    has_english_dub,
    sample_english_rate,
    variant_article,
    variant_names,
    variant_numbers,
    variant_subtitle,
)
from pipeline.stats import wilson_interval, wilson_lower

FILTERS = Path(__file__).resolve().parent.parent / "api" / "filters.py"


def make_row(**over) -> dict:
    row = {
        "id": 1,
        "title_romaji": "Wonder 3",
        "title_english": "The Amazing 3",
        "title_native": None,
        "synonyms": [],
        "start_year": 1965,
        "format": "TV",
        "country_of_origin": "JP",
        "english_va_count": 0,
        "cast_character_count": 0,
        "english_dub_inferred": False,
        "dub_languages": [],
        "dub_languages_source": None,
        "ann_id": None,
        "measured_dub_languages": [],
        "lang_checked": False,
        "en_checked": False,
    }
    row.update(over)
    return row


@pytest.mark.parametrize(
    "over",
    [
        {"english_va_count": 3},
        {"measured_dub_languages": ["en"]},
        {"dub_languages": ["en"]},
        {"english_dub_inferred": True},
    ],
)
def test_every_source_counts_as_covered(over):
    assert has_english_dub(make_row(**over))


def test_no_source_is_a_gap():
    assert not has_english_dub(make_row())
    # A dub in another language is not an English dub.
    assert not has_english_dub(make_row(dub_languages=["de", "fr"]))


def test_audit_predicate_matches_the_filter_the_ui_serves():
    """Drift guard, not a style check.

    has_english_dub restates api/filters.py's English clause in Python, and
    the audit's whole claim is that its gap set is the complaint set. If a
    fifth source is ever added to the filter, this fails and names the audit
    as the thing to update - the same check that caught the over-deleted
    workflow inputs, applied to a predicate instead of a YAML key.
    """
    source = FILTERS.read_text()
    start = source.index('if code == "en":')
    clause = source[start : source.index("else:", start)]
    columns = set(re.findall(r"m\.(\w+)", clause))
    assert columns == {
        "dub_languages",
        "measured_dub_languages",
        "english_va_count",
        "english_dub_inferred",
    }, columns


def test_unvisited_row_is_pending_not_a_source_limitation():
    """The rule the dub design rests on, applied to the audit.

    This row has cast_character_count = 0, which looks exactly like the
    38.2% no-cast case - but no pass has visited it, so the zero is a
    default, not an answer. Reporting it as anilist_no_cast would inflate
    the unfixable bucket with rows that are merely pending and send the next
    investigation after a gap that does not exist.
    """
    assert gap_cause(make_row()) == "pending_anilist"


def test_visited_row_with_no_cast_is_the_no_cast_case():
    assert gap_cause(make_row(en_checked=True, lang_checked=True)) == "anilist_no_cast"


def test_visited_row_with_cast_but_no_english():
    """The Berserk (2017) shape: 21 characters, no English, a real dub."""
    row = make_row(en_checked=True, lang_checked=True, cast_character_count=21)
    assert gap_cause(row) == "anilist_cast_without_english"


def test_measured_source_is_reported_over_inferred():
    """A measured dub must never be reported as inferred."""
    row = make_row(english_va_count=4, english_dub_inferred=True)
    assert dub_source(row) == "anilist_english_cast"
    row = make_row(measured_dub_languages=["en"], english_dub_inferred=True)
    assert dub_source(row) == "anilist_language_walk"
    assert dub_source(make_row(english_dub_inferred=True)) == "franchise_inferred"


def test_article_swap_goes_both_ways():
    assert "Amazing 3, The" in variant_article("The Amazing 3")
    assert "The Amazing 3" in variant_article("Amazing 3, The")
    assert variant_article("Discode") == []


def test_number_words_go_both_ways():
    assert "The Amazing three" in variant_numbers("The Amazing 3")
    assert "Wonder 3" in variant_numbers("Wonder Three")
    assert variant_numbers("Discode") == []


def test_subtitle_strip_keeps_a_usable_head():
    assert variant_subtitle("Gundam: Char's Counterattack") == ["Gundam"]
    # Too short a head would match half the catalog.
    assert variant_subtitle("W3: Wonder Three") == []
    assert variant_subtitle("Discode") == []


def test_variants_never_repeat_a_name_the_matcher_already_tried():
    """A variant that equals a known title inflates every count by one.

    The matcher already tries every AniList name, so re-offering one as a
    variant would report a hit the normal pass already had and credit the
    variant family with recovering a title it did not.
    """
    row = make_row(title_english="The Amazing 3", synonyms=["Amazing 3, The"])
    assert variant_names(row, "article_swap") == []


def test_variant_names_covers_every_family():
    row = make_row(title_english="The Amazing 3: Wonder Three")
    for family in VARIANTS:
        variant_names(row, family)  # must not raise for any family


class FakeCasts:
    """fetch_casts stand-in: ids absent from the map have no data."""

    def __init__(self, data):
        self.data = data

    def __call__(self, client, ids):
        return {i: self.data[i] for i in ids if i in self.data}


def pairs(*ids):
    return [(int(i), AnnEntry(ann_id=i, name="x", kind="TV", year=1965), "JP") for i in ids]


def test_missing_cast_data_is_not_counted_as_no_english(monkeypatch):
    """The failure that happened four times in this investigation.

    A swallowed HTTP 400 printed as '0/200 (0.0%)' and 404s were counted as
    rejections. Here, an ANN id with no cast on file must land in no_data,
    because folding it into 'no English' understates a class and would argue
    against a fix that is in fact worth making.
    """
    monkeypatch.setattr("pipeline.audit_dub.fetch_casts", FakeCasts({"1": (2, {"EN"})}))
    english, answered, no_data = sample_english_rate(
        None, pairs("1", "2", "3"), 10, random.Random(1)
    )
    assert (english, answered, no_data) == (1, 1, 2)


def test_empty_cast_list_is_also_no_data(monkeypatch):
    """ANN returning an entry with no cast means it has not filled it in."""
    monkeypatch.setattr("pipeline.audit_dub.fetch_casts", FakeCasts({"1": (0, set())}))
    assert sample_english_rate(None, pairs("1"), 10, random.Random(1)) == (0, 0, 1)


def test_native_language_is_not_counted_as_a_dub(monkeypatch):
    monkeypatch.setattr("pipeline.audit_dub.fetch_casts", FakeCasts({"1": (0, {"JA"})}))
    english, answered, _ = sample_english_rate(None, pairs("1"), 10, random.Random(1))
    assert (english, answered) == (0, 1)


def test_sampling_is_bounded_and_deterministic(monkeypatch):
    """Fixed seed, because head-biased sampling overstated Kitsu by ~3x."""
    monkeypatch.setattr(
        "pipeline.audit_dub.fetch_casts", FakeCasts({str(i): (1, {"EN"}) for i in range(50)})
    )
    first = sample_english_rate(None, pairs(*map(str, range(50))), 10, random.Random(7))
    second = sample_english_rate(None, pairs(*map(str, range(50))), 10, random.Random(7))
    assert first == second
    assert first[1] == 10


def test_wilson_interval_is_wide_when_the_sample_is_small():
    narrow = wilson_interval(50, 100)
    wide = wilson_interval(1, 2)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])
    assert wilson_interval(0, 0) == (0.0, 1.0)
    assert wilson_lower(0, 0) == 0.0


def test_wilson_bounds_contain_the_point_estimate():
    low, high = wilson_interval(7, 10)
    assert low <= 0.7 <= high
    assert wilson_lower(7, 10) == pytest.approx(low)
