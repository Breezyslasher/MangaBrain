"""Measuring every dub language per entry from AniList, in one request.

Every non-English dub in the database arrived via ANN, which matches on
title: 31.8% of the catalog resolves and 9,785 rows have no ANN entry at
all, so its non-English coverage is capped there permanently. AniList
answers for the id it was handed, so it reaches all 22,667 rows - it had
simply never been asked about anything but English.

Two wrong shapes were tried first, and both failed quietly, which is why
these tests pin the third.

Aliasing nine filtered fields into one request returns the same list under
every alias: Cowboy Bebop came back english=5, french=5, hebrew=5 and so
on, identical for all nine, while three titles with 20 to 66 English voice
actors returned nothing. Then a pass per language was built on the
conclusion that one request cannot carry several languages - it can. The
alias failure was specific to the language: argument being ignored and said
nothing about reading the language off the actor.

The working shape drops the filter and reads Staff.languageV2. Measured
against the filtered queries per language and per title: exact agreement,
one tenth the requests, and thirteen languages where the StaffLanguage enum
has ten - Chinese, Hindi and Indonesian are not in it.
"""

from pathlib import Path

import pytest

from pipeline.backfill_dub import NullVoiceActors
from pipeline.backfill_dub_languages import (
    CAST_QUERY,
    SELECT_SQL,
    UPDATE_SQL,
    languages_for,
)
from pipeline.dub_language_codes import LANGUAGE_V2_CODES, code_for, slug

SCHEMA = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()


def media(*actors: tuple[str, int], country: str = "JP") -> dict:
    """A media record whose cast credits (languageV2, count) pairs."""
    edges = []
    next_id = 900
    for language, count in actors:
        for _ in range(count):
            edges.append(
                {"node": {"id": next_id}, "voiceActors": [{"id": next_id, "languageV2": language}]}
            )
            next_id += 1
    return {"countryOfOrigin": country, "characters": {"edges": edges}}


# --- the query shape ---------------------------------------------------


def test_the_query_does_not_filter_by_language():
    """Filtering is what forced one request per language."""
    assert "voiceActors {" in CAST_QUERY
    assert "language:" not in CAST_QUERY


def test_the_query_reads_language_off_the_actor():
    assert "languageV2" in CAST_QUERY


def test_the_query_keeps_the_node_selection():
    """Without node, voiceActors resolves to null on every edge, silently."""
    assert "node { id }" in CAST_QUERY


def test_the_query_asks_for_country_of_origin():
    """Needed to know which language is the original rather than a dub."""
    assert "countryOfOrigin" in CAST_QUERY


def test_the_query_pages_over_media():
    assert "Page(" in CAST_QUERY
    assert "id_in: $ids" in CAST_QUERY
    assert "$perPage" in CAST_QUERY


def test_no_placeholder_is_left_unreplaced():
    assert "CHARACTERS" not in CAST_QUERY
    assert "perPage: 25" in CAST_QUERY


# --- reading languages -------------------------------------------------


def test_every_language_in_one_record_is_collected():
    """The whole point: one request, all languages."""
    codes, unknown = languages_for(
        media(("ENGLISH", 2), ("SPANISH", 3), ("GERMAN", 1), ("INDONESIAN", 1))
    )
    assert codes == ["de", "en", "es", "id"]
    assert unknown == []


def test_languages_outside_the_enum_are_kept():
    """StaffLanguage has ten values; one request returned thirteen."""
    codes, _ = languages_for(media(("CHINESE", 1), ("HINDI", 1), ("INDONESIAN", 1)))
    assert codes == ["hi", "id", "zh"]
    for enum_absent in ("CHINESE", "HINDI", "INDONESIAN"):
        assert enum_absent in LANGUAGE_V2_CODES


def test_a_titles_own_language_is_not_a_dub():
    codes, _ = languages_for(media(("JAPANESE", 5), ("ENGLISH", 2), country="JP"))
    assert codes == ["en"]


def test_the_same_language_is_a_dub_elsewhere():
    """A Japanese cast on a donghua is a dub; dropping 'ja' outright is wrong."""
    codes, _ = languages_for(media(("JAPANESE", 3), ("ENGLISH", 1), country="CN"))
    assert codes == ["en", "ja"]


def test_an_unknown_country_strips_nothing():
    codes, _ = languages_for(media(("JAPANESE", 1), ("ENGLISH", 1), country=""))
    assert codes == ["en", "ja"]


def test_a_cast_with_no_dub_yields_nothing_rather_than_failing():
    codes, unknown = languages_for(media(("JAPANESE", 8), country="JP"))
    assert codes == []
    assert unknown == []


def test_an_unmapped_language_is_reported_not_dropped():
    """Missing from the mapping is not missing from the data."""
    codes, unknown = languages_for(media(("KLINGON", 1), ("ENGLISH", 1)))
    assert "klingon" in codes
    assert unknown == ["KLINGON"]


def test_null_voice_actors_raises_rather_than_reading_as_empty():
    """The failure that cost the English walk twelve hours a pass."""
    with pytest.raises(NullVoiceActors):
        languages_for(
            {
                "countryOfOrigin": "JP",
                "characters": {"edges": [{"node": {"id": 1}, "voiceActors": None}]},
            }
        )


# --- the code table ----------------------------------------------------


def test_the_enum_languages_are_all_mapped():
    for language in (
        "JAPANESE",
        "ENGLISH",
        "KOREAN",
        "ITALIAN",
        "SPANISH",
        "PORTUGUESE",
        "FRENCH",
        "GERMAN",
        "HEBREW",
        "HUNGARIAN",
    ):
        assert language in LANGUAGE_V2_CODES


def test_regions_are_kept_only_where_they_separate_dub_markets():
    assert LANGUAGE_V2_CODES["BRAZILIAN PORTUGUESE"] == "pt-br"
    assert LANGUAGE_V2_CODES["CANTONESE"] == "zh-hk"
    # ...and collapsed otherwise.
    assert LANGUAGE_V2_CODES["GERMAN"] == "de"


def test_code_for_reports_whether_it_knew_the_language():
    assert code_for("ENGLISH") == ("en", True)
    assert code_for("english") == ("en", True)
    assert code_for("Nonsense Tongue") == ("nonsense-tongue", False)


def test_an_absent_language_is_named_rather_than_dropped():
    assert code_for(None) == ("unknown", False)
    assert code_for("  ") == ("unknown", False)


def test_slug_is_stable_and_never_empty():
    assert slug("Some  Language") == "some-language"
    assert slug("") == "unknown"


# --- persistence -------------------------------------------------------


def test_selection_skips_rows_already_measured():
    assert "measured_dub_checked_at IS NULL" in SELECT_SQL
    assert "media_type = 'ANIME'" in SELECT_SQL


def test_selection_orders_unmeasured_first_then_newest():
    """A freshly synced title should not wait out the whole backlog."""
    assert "measured_dub_checked_at ASC NULLS FIRST" in SELECT_SQL
    assert "m.id DESC" in SELECT_SQL


def test_the_update_records_the_measurement_and_its_time():
    assert "measured_dub_languages" in UPDATE_SQL
    assert "measured_dub_checked_at = now()" in UPDATE_SQL


def test_the_columns_exist_in_the_schema():
    assert "measured_dub_languages" in SCHEMA
    assert "measured_dub_checked_at" in SCHEMA


# --- integration with the filter ---------------------------------------


def test_the_filter_accepts_either_source():
    from api.filters import build_filters

    sql, params = build_filters(dub_lang="es")
    joined = " ".join(sql) if isinstance(sql, list) else str(sql)
    assert "measured_dub_languages" in joined
    assert "dub_languages" in joined
    assert params["f_dub_lang"] == "es"


def test_english_still_reaches_all_of_its_sources():
    from api.filters import build_filters

    sql, _params = build_filters(dubbed=True)
    joined = " ".join(sql) if isinstance(sql, list) else str(sql)
    for source in (
        "english_va_count",
        "english_dub_inferred",
        "measured_dub_languages",
        "dub_languages",
    ):
        assert source in joined


def test_a_measured_language_outranks_a_name_match():
    from api.db import _dub_source

    assert _dub_source({"measured_dub_languages": ["en"], "dub_languages": ["en"]}) == "measured"
    assert _dub_source({"dub_languages": ["en"]}) == "ann"
    assert _dub_source({"english_dub_inferred": True}) == "franchise"
    assert _dub_source({"english_va_count": None}) is None


def test_a_measured_language_counts_as_having_a_dub():
    from api.db import _has_dub

    assert _has_dub({"measured_dub_languages": ["en"]}) is True
    assert _has_dub({"english_va_count": None}) is None
    assert _has_dub({"english_va_count": 0}) is False
