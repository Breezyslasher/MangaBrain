"""Taste profile and For-you draw on every configured list source.

Both used to take exactly one source (AniList, else MAL, else the named
lists), so a user with an AniList account got nothing from a synced Kitsu,
Yamtrack or Suwayomi library. Ratings are still ranked within each source,
because people rate differently and rate differently on different sites.
"""

import numpy as np

from api.routers.foryou import round_robin
from api.routers.recommend import _taste_profile, taste_sources, taste_weights
from tests.fake_db import FakeConn

E1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
E2 = np.array([0.0, 1.0, 0.0], dtype=np.float32)
E3 = np.array([0.0, 0.0, 1.0], dtype=np.float32)


def rows_for(sql_marker: str, rows: dict[str, list[dict]]):
    def responder(sql, params):
        for marker, value in rows.items():
            if marker in sql:
                return value
        return []

    return responder


def normalized(profile: np.ndarray) -> np.ndarray:
    return profile / np.linalg.norm(profile)


def expected(pairs: list[tuple[np.ndarray, float]]) -> np.ndarray:
    vectors = np.array([v for v, _ in pairs])
    weights = np.array([w for _, w in pairs])
    return normalized((vectors * weights[:, None]).sum(axis=0) / weights.sum())


def test_sources_lists_every_configured_source():
    assert len(taste_sources("nick", "nick", ["suwayomi"])) == 3
    assert len(taste_sources("nick", None, None)) == 1
    assert taste_sources(None, None, [" "]) == []


def test_single_source_keeps_the_previous_weights():
    rows = [
        {"media_id": 1, "embedding": E1, "score": 100},
        {"media_id": 2, "embedding": E2, "score": 0},
    ]
    conn = FakeConn(rows_for("x", {"anilist_list_entries": rows}))
    profile = _taste_profile(conn, "m", anilist_user="nick", mal_user=None, exclude_lists=None)
    weights = taste_weights([100, 0])
    assert np.allclose(profile, expected([(E1, weights[0]), (E2, weights[1])]))


def test_a_title_on_two_lists_counts_once_at_its_highest_weight():
    anilist = [
        {"media_id": 1, "embedding": E1, "score": 100},
        {"media_id": 2, "embedding": E2, "score": 0},
    ]
    custom = [
        {"media_id": 2, "embedding": E2, "score": 100},
        {"media_id": 3, "embedding": E3, "score": 0},
    ]
    conn = FakeConn(
        rows_for("x", {"anilist_list_entries": anilist, "custom_exclusion_entries": custom})
    )
    profile = _taste_profile(
        conn, "m", anilist_user="nick", mal_user=None, exclude_lists=["suwayomi"]
    )
    top, bottom = taste_weights([100, 0])
    # media 2 is on both lists: counted once, at the higher of its weights.
    assert np.allclose(profile, expected([(E1, top), (E2, top), (E3, bottom)]))


def test_weights_are_ranked_within_each_source():
    # A source whose scores are all low still contributes a full spread,
    # rather than being flattened against another source's higher numbers.
    low = [
        {"media_id": 1, "embedding": E1, "score": 20},
        {"media_id": 2, "embedding": E2, "score": 10},
    ]
    conn = FakeConn(rows_for("x", {"anilist_list_entries": low}))
    profile = _taste_profile(conn, "m", anilist_user="nick", mal_user=None, exclude_lists=None)
    top, bottom = taste_weights([20, 10])
    assert top > bottom
    assert np.allclose(profile, expected([(E1, top), (E2, bottom)]))


def test_no_source_and_no_rows_give_no_profile():
    conn = FakeConn(lambda sql, params: [])
    assert _taste_profile(conn, "m", anilist_user=None, mal_user=None, exclude_lists=None) is None
    assert _taste_profile(conn, "m", anilist_user="nick", mal_user=None, exclude_lists=None) is None


def test_round_robin_interleaves_and_dedupes():
    assert round_robin([[1, 2, 3], [4, 5]], 10) == [1, 4, 2, 5, 3]
    assert round_robin([[1, 2], [2, 3]], 10) == [1, 2, 3]


def test_round_robin_gives_a_small_list_a_share_of_a_small_seed_count():
    big = list(range(100))
    small = [900, 901]
    # Concatenating would fill all four seeds from the 100-title library.
    assert round_robin([big, small], 4) == [0, 900, 1, 901]


def test_round_robin_with_one_source_is_that_sources_order():
    assert round_robin([[7, 3, 9]], 8) == [7, 3, 9]
    assert round_robin([[7, 3, 9]], 2) == [7, 3]
    assert round_robin([], 5) == []
