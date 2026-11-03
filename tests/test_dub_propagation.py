"""Inferring an English dub across a franchise.

Measured against ANN: of the AniList-silent titles that resolved exactly, 0
of 7 had an English cast, so AniList's silence about a standalone entry is
right. The false negatives were side entries - BEASTARS FINAL SEASON Part 2
records 25 characters and no English voice actor on AniList, while ANN
credits 18 English cast entries to the series it belongs to. Propagation
recovers those without ever overwriting a measured value.

The relation whitelist is the whole risk, so it is pinned here. The SQL shapes
are asserted rather than executed because CI has no database; the recursion
itself was verified against Postgres 16 on a graph covering a two-hop sequel
chain, a recap, a spin-off, a source manga and a stale flag.
"""

import inspect
from pathlib import Path

from api.db import MEDIA_COLS, media_from_row
from api.filters import build_filters
from pipeline.propagate_dub import CLEAR_SQL, MARK_SQL, PROPAGATE_RELATIONS, REACHABLE_SQL

SCHEMA = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()


def test_only_same_production_relations_propagate():
    assert set(PROPAGATE_RELATIONS) == {"SEQUEL", "PREQUEL", "PARENT", "SIDE_STORY"}


def test_relations_that_imply_nothing_about_a_dub_are_excluded():
    """Each of these would assert a dub the franchise does not support.

    A recap of a dubbed series is frequently not dubbed; an alternative
    version is often a different production; a spin-off is a different show;
    and the source manga has no dub at all.
    """
    for excluded in (
        "SUMMARY",
        "COMPILATION",
        "ALTERNATIVE",
        "SPIN_OFF",
        "ADAPTATION",
        "SOURCE",
        "CHARACTER",
        "OTHER",
    ):
        assert excluded not in PROPAGATE_RELATIONS


def test_the_walk_is_bounded_bidirectional_and_anime_only():
    # Unbounded, a franchise component could chain arbitrarily far, and a dub
    # is a weaker claim with every hop.
    assert "w.depth < %(max_depth)s" in REACHABLE_SQL
    # AniList stores a relation one way only, so edges are unioned both ways.
    assert REACHABLE_SQL.count("FROM media_relations r") == 2
    assert "SELECT r.related_id, r.media_id" in REACHABLE_SQL
    # Manga has no dub.
    assert REACHABLE_SQL.count("media_type = 'ANIME'") == 2


def test_the_walk_starts_only_from_measured_dubs():
    assert "m.english_va_count > 0" in REACHABLE_SQL
    # And returns only entries that are not themselves measured as dubbed.
    assert "COALESCE(m.english_va_count, 0) = 0" in REACHABLE_SQL


def test_marking_never_overwrites_a_measured_value():
    assert "english_dub_inferred = TRUE" in MARK_SQL
    assert "COALESCE(english_va_count, 0) = 0" in MARK_SQL
    assert "english_va_count =" not in MARK_SQL


def test_a_flag_no_longer_supported_is_cleared():
    # A removed relation, or a sibling whose measured cast went away, must
    # not leave an inferred dub behind for ever.
    assert "english_dub_inferred = FALSE" in CLEAR_SQL
    assert "NOT (id = ANY(%(ids)s))" in CLEAR_SQL


def test_the_filter_accepts_measured_or_inferred():
    sql, _ = build_filters(dubbed=True)
    assert "(m.english_va_count > 0 OR m.english_dub_inferred)" in sql


def test_the_filter_is_still_positive_only():
    code = [
        line
        for line in inspect.getsource(build_filters).splitlines()
        if not line.strip().startswith("#")
    ]
    assert not [line for line in code if "english_dub_inferred = FALSE" in line]
    assert not [line for line in code if "NOT m.english_dub_inferred" in line]


def test_schema_defaults_inferred_to_false_and_indexes_the_positive():
    assert "english_dub_inferred BOOLEAN NOT NULL DEFAULT FALSE" in SCHEMA
    assert "WHERE english_dub_inferred" in SCHEMA


def test_dub_source_distinguishes_measured_from_franchise():
    row = _row(english_va_count=7, english_dub_inferred=False)
    out = media_from_row(row)
    assert (out.has_english_dub, out.dub_source) == (True, "measured")

    row = _row(english_va_count=0, english_dub_inferred=True)
    out = media_from_row(row)
    assert (out.has_english_dub, out.dub_source) == (True, "franchise")

    # Never checked and never inferred stays unknown, not False.
    row = _row(english_va_count=None, english_dub_inferred=False)
    out = media_from_row(row)
    assert (out.has_english_dub, out.dub_source) == (None, None)

    # Checked, nothing found, no dubbed relative: a real negative.
    row = _row(english_va_count=0, english_dub_inferred=False)
    out = media_from_row(row)
    assert (out.has_english_dub, out.dub_source) == (False, None)


def test_an_inferred_dub_counts_even_when_never_measured():
    # A row the backfill has not reached yet can still inherit from a sibling.
    out = media_from_row(_row(english_va_count=None, english_dub_inferred=True))
    assert out.has_english_dub is True
    assert out.dub_source == "franchise"


def test_media_cols_select_the_inferred_flag():
    assert "m.english_dub_inferred" in MEDIA_COLS


def _row(**overrides) -> dict:
    row = {
        "id": 1,
        "id_mal": None,
        "medium": "anime",
        "title_romaji": "T",
        "title_english": None,
        "title_native": None,
        "description": None,
        "genres": [],
        "tags": [],
        "format": None,
        "episodes": None,
        "chapters": None,
        "volumes": None,
        "country_of_origin": None,
        "start_year": None,
        "status": None,
        "average_score": None,
        "cover_image_medium": None,
        "cover_image_large": None,
        "is_adult": False,
        "synonyms": [],
        "popularity": None,
        "favourites": None,
        "english_va_count": None,
        "dub_groups": [],
        "english_dub_inferred": False,
    }
    row.update(overrides)
    return row
