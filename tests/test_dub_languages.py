"""Measuring dub languages per entry from AniList.

Every non-English dub in the database arrived via ANN, which matches on
title: 31.8% of the catalog resolves and 9,785 rows have no ANN entry at
all, so its non-English coverage is capped there permanently. AniList
answers for the id it was handed, so it reaches all 22,667 rows - it had
simply never been asked about anything but English.

The trap these tests exist to hold shut is the aliased query. Asking for
nine languages as nine aliased fields in one request looked free and
silently returned the same list under every alias: Cowboy Bebop came back
english=5, french=5, hebrew=5 and so on, while three titles with 20 to 66
English voice actors returned nothing. No error, plausible shape. One
request per language is the shape that reproduces the control.
"""

from pathlib import Path

import pytest

from pipeline.backfill_dub_languages import (
    CAST_QUERY_TEMPLATE,
    DEFAULT_LANGUAGES,
    LANGUAGE_CODES,
    SELECT_SQL,
    UPDATE_SQL,
    build_query,
    has_cast,
    is_native,
)
from pipeline.backfill_dub import NullVoiceActors

SCHEMA = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()


def edges(*counts: int) -> dict:
    """A media record whose characters credit the given numbers of actors."""
    return {
        "characters": {
            "edges": [
                {"node": {"id": i}, "voiceActors": [{"id": 900 + i}] * n}
                for i, n in enumerate(counts)
            ]
        }
    }


def test_the_query_asks_for_exactly_one_language():
    """Nine aliased fields return one list under every alias."""
    query = build_query("SPANISH")
    assert "voiceActors(language: SPANISH)" in query
    assert query.count("voiceActors(") == 1
    for other in ("ENGLISH", "FRENCH", "GERMAN"):
        assert f"language: {other}" not in query


def test_the_query_keeps_the_node_selection():
    """Without node, voiceActors resolves to null on every edge, silently."""
    for language in LANGUAGE_CODES:
        assert "node { id }" in build_query(language)


def test_the_query_pages_over_media():
    query = build_query("ITALIAN")
    assert "Page(" in query
    assert "id_in: $ids" in query
    assert "$perPage" in query


def test_an_unknown_language_is_refused_not_interpolated():
    """A bad enum value would otherwise reach AniList inside the query."""
    with pytest.raises(ValueError):
        build_query("KLINGON")
    with pytest.raises(ValueError):
        build_query("'; DROP TABLE media; --")


def test_every_language_has_an_iso_code():
    assert LANGUAGE_CODES["SPANISH"] == "es"
    assert LANGUAGE_CODES["PORTUGUESE"] == "pt"
    assert LANGUAGE_CODES["HEBREW"] == "he"
    assert LANGUAGE_CODES["HUNGARIAN"] == "hu"
    # Ten values, introspected from StaffLanguage rather than recalled.
    assert len(LANGUAGE_CODES) == 10


def test_english_is_not_in_the_default_pass():
    """backfill_dub already measures it; re-asking costs an hour for nothing."""
    assert "ENGLISH" not in DEFAULT_LANGUAGES
    # But it stays addressable explicitly.
    assert "ENGLISH" in LANGUAGE_CODES


def test_the_default_order_leads_with_the_measured_winners():
    """A time-boxed run should spend its budget where the yield is.

    Measured on five titles: SPANISH returned 154 voice actor credits,
    PORTUGUESE 124, ITALIAN and GERMAN 111, against HEBREW's 5.
    """
    assert DEFAULT_LANGUAGES[0] == "SPANISH"
    assert DEFAULT_LANGUAGES.index("PORTUGUESE") < DEFAULT_LANGUAGES.index("HEBREW")
    assert DEFAULT_LANGUAGES.index("ITALIAN") < DEFAULT_LANGUAGES.index("HUNGARIAN")


def test_a_credited_actor_counts_as_a_cast():
    assert has_cast(edges(1, 0, 0)) is True
    assert has_cast(edges(0, 0, 3)) is True


def test_no_credited_actor_is_not_a_cast():
    assert has_cast(edges(0, 0, 0)) is False
    assert has_cast({"characters": {"edges": []}}) is False


def test_null_voice_actors_raises_rather_than_reading_as_empty():
    """The failure that cost the English walk twelve hours a pass."""
    with pytest.raises(NullVoiceActors):
        has_cast({"characters": {"edges": [{"node": {"id": 1}, "voiceActors": None}]}})


def test_a_titles_own_language_is_not_a_dub():
    assert is_native("ja", "JP") is True
    assert is_native("ko", "KR") is True


def test_the_same_language_is_a_dub_elsewhere():
    """A Japanese cast on a donghua is a dub; dropping 'ja' outright is wrong."""
    assert is_native("ja", "CN") is False
    assert is_native("ja", None) is False
    assert is_native("es", "JP") is False


def test_selection_skips_languages_already_asked_about():
    """Resumability: the pass must not re-ask what it has asked."""
    assert "dub_languages_checked" in SELECT_SQL
    assert "NOT (%(code)s = ANY(m.dub_languages_checked))" in SELECT_SQL
    assert "media_type = 'ANIME'" in SELECT_SQL


def test_the_update_records_the_question_as_well_as_the_answer():
    """Asked-and-found-nothing must stay distinct from never-asked."""
    assert "measured_dub_languages" in UPDATE_SQL
    assert "dub_languages_checked" in UPDATE_SQL
    # DISTINCT, so a re-run cannot accumulate duplicates in either array.
    assert UPDATE_SQL.count("array_agg(DISTINCT lang") == 2


def test_the_update_only_records_a_language_when_one_was_found():
    assert "CASE WHEN %(found)s" in UPDATE_SQL


def test_the_columns_exist_in_the_schema():
    assert "measured_dub_languages" in SCHEMA
    assert "dub_languages_checked" in SCHEMA
    assert "idx_media_measured_dub_languages" in SCHEMA


def test_the_template_has_no_stray_placeholder_left():
    """A LANGUAGE or CHARACTERS left unreplaced would be sent verbatim."""
    query = build_query("GERMAN")
    assert "LANGUAGE" not in query
    assert "CHARACTERS" not in query
    assert "perPage: 25" in query


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
    # Unknown stays unknown rather than becoming False.
    assert _has_dub({"english_va_count": None}) is None
    assert _has_dub({"english_va_count": 0}) is False
