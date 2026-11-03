"""A cast in the title's own language is not a dub.

ANN tags every cast entry with its language and draws no distinction between
the original performance and a dub, so copying its languages across recorded
Japanese as the most common "dub language" in the catalog: 6,470 rows against
English's 2,901. Any row whose only ANN cast was Japanese also looked like a
title carrying dub data when it has none.

The correction cannot just drop 'ja'. A Japanese cast on a Chinese donghua or
a Korean webtoon adaptation genuinely is a dub, so what gets dropped is the
language implied by the title's own country_of_origin.
"""

from pathlib import Path

from pipeline.backfill_dub_ann import (
    NATIVE_LANGS,
    STRIP_NATIVE_SQL,
    dub_languages_for,
)

SCHEMA = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()


def test_japanese_is_not_a_dub_on_a_japanese_show():
    langs = {"JA", "EN", "DE"}
    assert dub_languages_for(langs, "JP") == ["de", "en"]


def test_japanese_IS_a_dub_on_a_chinese_show():
    """The case that rules out simply dropping 'ja' everywhere."""
    assert dub_languages_for({"JA", "ZH-CN"}, "CN") == ["ja"]


def test_korean_is_not_a_dub_on_a_korean_show():
    assert dub_languages_for({"KO", "EN"}, "KR") == ["en"]
    # ...but it is on a Japanese one.
    assert dub_languages_for({"KO", "EN"}, "JP") == ["en", "ko"]


def test_every_chinese_variant_counts_as_native_for_a_chinese_show():
    """ANN uses language-region tags for Chinese, not a single code."""
    assert dub_languages_for({"ZH-CN", "ZH-HK", "ZH-TW", "EN"}, "CN") == ["en", "zh-hk", "zh-tw"]
    assert dub_languages_for({"ZH-TW", "EN"}, "TW") == ["en"]
    assert dub_languages_for({"ZH-HK", "EN"}, "HK") == ["en"]


def test_an_unknown_or_missing_country_strips_nothing():
    """Better to keep a language than to guess a title's original wrongly."""
    assert dub_languages_for({"JA", "EN"}, None) == ["en", "ja"]
    assert dub_languages_for({"JA", "EN"}, "XX") == ["en", "ja"]


def test_case_and_order_are_normalised():
    assert dub_languages_for({"En", "DE", "es"}, "JP") == ["de", "en", "es"]


def test_a_title_dubbed_only_into_its_own_language_ends_up_empty():
    """Which is the point: it has no dubs, and must not look like it does."""
    assert dub_languages_for({"JA"}, "JP") == []


def test_the_cleanup_is_scoped_and_idempotent():
    # Only anime, only the matching country, and only rows that actually
    # carry a native entry - so a second run changes nothing.
    assert "media_type = 'ANIME'" in STRIP_NATIVE_SQL
    assert "country_of_origin = %(country)s" in STRIP_NATIVE_SQL
    assert "dub_languages && %(native)s" in STRIP_NATIVE_SQL
    # Rebuilds the array without the native codes rather than clearing it.
    assert "array_agg" in STRIP_NATIVE_SQL
    assert "lang <> ALL(%(native)s)" in STRIP_NATIVE_SQL


def test_japan_is_covered_since_it_is_almost_the_whole_catalog():
    assert NATIVE_LANGS["JP"] == {"ja"}
    assert "ja" not in NATIVE_LANGS["CN"]


def test_the_column_the_cleanup_relies_on_exists():
    assert "country_of_origin" in SCHEMA
