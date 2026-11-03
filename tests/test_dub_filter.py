"""The English dub signal: a positive filter that must never be inverted.

AniList has no dub field, so the signal is "AniList credits an English voice
actor" (pipeline.backfill_dub). Measured coverage is 100% at the top of the
catalog but ~60-67% around rank 3000-5000, and the misses are real dubs:
Berserk 2 (2017) returns 21 characters and zero English voice actors. So a
positive is trustworthy and a zero is not, and these tests pin that asymmetry
into the filter, the schema and the API surface.
"""

import inspect
from pathlib import Path

import pytest

from api.db import MEDIA_COLS, media_from_row
from api.filters import build_filters
from pipeline.backfill_dub import NullVoiceActors, parse_cast
from pipeline.snapshot import DATA_TABLES, USER_TABLES

SCHEMA = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()


def test_dubbed_true_filters_on_a_credited_english_cast():
    sql, _ = build_filters(dubbed=True)
    assert "m.english_va_count > 0" in sql


def test_dubbed_false_filters_nothing():
    # Off must mean "no dub filtering", not "sub only".
    sql, _ = build_filters(dubbed=False)
    assert "english_va_count" not in sql
    assert build_filters()[0] == sql


def test_the_filter_has_no_sub_only_branch():
    """A zero English cast is not evidence of a sub-only title.

    If someone later adds `english_va_count = 0` to express "sub only", it
    would hide dubbed midlist titles - precisely the obscure end of the
    catalog this project exists to surface. This test is the tripwire.
    """
    # Comments discuss the column at length, so look at code lines only.
    code = [
        line
        for line in inspect.getsource(build_filters).splitlines()
        if not line.strip().startswith("#")
    ]
    mentions = [line for line in code if "english_va_count" in line]
    assert len(mentions) == 1, mentions
    assert "english_va_count > 0" in mentions[0]


def test_unchecked_is_distinct_from_checked_and_empty():
    row = _media_row()
    row["english_va_count"] = None
    assert media_from_row(row).has_english_dub is None
    row["english_va_count"] = 0
    assert media_from_row(row).has_english_dub is False
    row["english_va_count"] = 7
    assert media_from_row(row).has_english_dub is True


def test_dub_groups_are_exposed_and_default_empty():
    row = _media_row()
    row["dub_groups"] = ["4Kids", "Odex"]
    assert media_from_row(row).dub_groups == ["4Kids", "Odex"]
    row["dub_groups"] = None
    assert media_from_row(row).dub_groups == []


def test_rows_missing_the_columns_still_map():
    # An older snapshot restored before the migration ran.
    row = _media_row()
    row.pop("english_va_count")
    row.pop("dub_groups")
    out = media_from_row(row)
    assert out.has_english_dub is None
    assert out.dub_groups == []


def test_media_cols_select_the_dub_columns():
    assert "m.english_va_count" in MEDIA_COLS
    assert "m.dub_groups" in MEDIA_COLS


def test_schema_keeps_unchecked_nullable_and_indexes_only_the_positive():
    assert "ADD COLUMN IF NOT EXISTS english_va_count INTEGER;" in SCHEMA
    # No NOT NULL DEFAULT 0: that would make every unchecked row read as
    # "checked, no English cast".
    assert "english_va_count INTEGER NOT NULL" not in SCHEMA
    assert "WHERE english_va_count > 0" in SCHEMA


def test_the_signal_ships_in_the_public_snapshot_but_its_bookkeeping_does_not():
    # The columns live on media, so users get the backfill for free rather
    # than spending hours of AniList requests to rebuild it.
    assert "media" in DATA_TABLES
    assert "dub_backfill_state" in USER_TABLES
    assert "dub_backfill_state" not in DATA_TABLES


def test_parse_cast_counts_distinct_actors_edges_and_groups():
    media = {
        "characters": {
            "edges": [
                {
                    "node": {"id": 1},
                    "voiceActors": [{"id": 9}],
                    "voiceActorRoles": [{"dubGroup": "4Kids"}],
                },
                {
                    "node": {"id": 2},
                    # The same actor across two characters counts once.
                    "voiceActors": [{"id": 9}, {"id": 10}],
                    "voiceActorRoles": [{"dubGroup": None}, {"dubGroup": "Odex"}],
                },
            ]
        }
    }
    assert parse_cast(media) == (2, 2, ["4Kids", "Odex"])


def test_parse_cast_distinguishes_no_cast_from_no_english_cast():
    no_cast = {"characters": {"edges": []}}
    assert parse_cast(no_cast) == (0, 0, [])
    japanese_only = {
        "characters": {"edges": [{"node": {"id": 1}, "voiceActors": [], "voiceActorRoles": []}]}
    }
    assert parse_cast(japanese_only) == (0, 1, [])


def test_null_voice_actors_raises_instead_of_counting_zero():
    """The bug that produced a catalog-wide false zero must fail loudly.

    Without a `node` selection AniList resolves voiceActors to null on every
    edge rather than erroring, which is indistinguishable from "no English
    cast" unless something checks.
    """
    broken = {"characters": {"edges": [{"node": {"id": 1}, "voiceActors": None}]}}
    with pytest.raises(NullVoiceActors):
        parse_cast(broken)


def test_backfill_query_keeps_the_node_selection():
    from pipeline.backfill_dub import CAST_QUERY

    assert "node { id }" in CAST_QUERY
    assert "voiceActors(language: ENGLISH)" in CAST_QUERY
    # ANIME only: manga has no dub.
    assert "type: ANIME" in CAST_QUERY


def test_backfill_only_walks_anime():
    from pipeline.backfill_dub import SELECT_SQL

    assert "m.media_type = 'ANIME'" in SELECT_SQL
    # Unchecked rows first so an interrupted multi-hour walk resumes.
    assert "dub_checked_at ASC NULLS FIRST" in SELECT_SQL


def test_unchecked_titles_are_walked_newest_first():
    """Every unchecked row ties on dub_checked_at, so the tiebreak decides.

    Ascending by id walks the oldest catalog entries first and starves newly
    synced titles of a dub flag for as long as the backlog lasts, which at a
    few hundred per nightly pass is weeks. AniList ids grow over time, so
    descending checks the newest first.
    """
    from pipeline.backfill_dub import SELECT_SQL

    assert "m.id DESC" in SELECT_SQL


def test_nightly_rechecks_stale_rows_so_a_later_dub_is_picked_up(monkeypatch):
    """A dub is often added months after a title airs.

    Without a recheck window a title checked once before its dub existed
    stays flagged as having none forever.
    """
    import pipeline.nightly as nightly
    from api.config import settings as cfg

    captured: dict = {}

    def fake_backfill(client, conn, limit=None, stale_before=None, **kwargs):
        captured["limit"] = limit
        captured["stale_before"] = stale_before

    monkeypatch.setattr(nightly, "backfill_dub", fake_backfill)
    monkeypatch.setattr(cfg, "dub_backfill_per_pass", 400)

    monkeypatch.setattr(cfg, "dub_recheck_days", 90)
    nightly._dub_pass(object(), object())
    assert captured["limit"] == 400
    assert captured["stale_before"] is not None

    # 0 means never recheck: only never-checked titles are walked.
    monkeypatch.setattr(cfg, "dub_recheck_days", 0)
    nightly._dub_pass(object(), object())
    assert captured["stale_before"] is None


def test_dub_signal_is_not_a_scoring_input():
    """Popularity is banned from scoring; so is this.

    A dub flag correlates hard with licensing, which correlates with
    popularity, so letting it into the ranking function would smuggle
    popularity back in through the side door.
    """
    from api import scoring

    source = inspect.getsource(scoring)
    for banned in ("english_va_count", "dub", "has_english_dub"):
        assert banned not in source


def _media_row() -> dict:
    return {
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
    }


def test_dub_pass_is_skippable_and_cannot_break_the_nightly_run(monkeypatch):
    """The dubbed filter's freshness must never cost the catalog its sync."""
    import pipeline.nightly as nightly
    from api.config import settings as cfg

    calls: list[int | None] = []

    def fake_backfill(client, conn, limit=None, **kwargs):
        calls.append(limit)
        raise RuntimeError("AniList said no")

    monkeypatch.setattr(nightly, "backfill_dub", fake_backfill)

    monkeypatch.setattr(cfg, "dub_backfill_per_pass", 0)
    nightly._dub_pass(object(), object())
    assert calls == []  # disabled means not contacted at all

    monkeypatch.setattr(cfg, "dub_backfill_per_pass", 250)
    nightly._dub_pass(object(), object())  # must not raise
    assert calls == [250]


def test_coverage_counts_checked_separately_from_dubbed(monkeypatch):
    from fake_db import FakePool
    from fastapi.testclient import TestClient

    import api.main as main
    import api.routers.dub as dub_router
    from api.config import settings as cfg

    monkeypatch.setattr(cfg, "auth_token", "")
    monkeypatch.setattr(cfg, "rate_limit_per_minute", 0)
    main._rate_windows.clear()
    monkeypatch.setattr(
        dub_router,
        "get_pool",
        lambda: FakePool(
            lambda sql, params: [{"total": 100, "checked": 40, "dubbed": 25, "without_cast": 6}]
        ),
    )
    body = TestClient(main.app, raise_server_exceptions=False).get("/dub/coverage").json()
    assert body == {
        "total": 100,
        "checked": 40,
        "dubbed": 25,
        "without_cast": 6,
        # 40 of 100 checked: the filter is still hiding unchecked titles.
        "complete": False,
    }


def test_coverage_only_counts_anime():
    from api.routers.dub import COVERAGE_SQL

    assert "media_type = 'ANIME'" in COVERAGE_SQL
    # count(english_va_count) skips NULLs, which is what "checked" means.
    assert "count(english_va_count)" in COVERAGE_SQL
