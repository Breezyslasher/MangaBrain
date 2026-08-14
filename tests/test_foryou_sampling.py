"""Rating-weighted seed sampling for the For-you feed.

The SQL implements Efraimidis-Spirakis A-Res: key = u^(1/w) per row with
w = (score/100)^2, top-n keys win. These tests pin the SQL shape and verify
the math produces the intended selection bias when simulated in Python.
"""

import random

from api.routers.foryou import (
    ANILIST_SEEDS_SQL,
    LIST_SEEDS_SQL,
    MAL_SEEDS_SQL,
    weighted_order,
)


def test_weighted_order_shape():
    clause = weighted_order("al.score")
    assert "POWER(random(), 10000.0" in clause
    assert "COALESCE(al.score, 60)" in clause  # unrated = neutral weight
    assert "GREATEST" in clause and "10" in clause  # floor keeps 1/w bounded


def test_all_three_seed_sources_are_weighted():
    assert weighted_order("al.score") in ANILIST_SEEDS_SQL
    assert weighted_order("l.score") in MAL_SEEDS_SQL
    assert weighted_order("ce.score") in LIST_SEEDS_SQL
    for sql in (ANILIST_SEEDS_SQL, MAL_SEEDS_SQL, LIST_SEEDS_SQL):
        assert "ORDER BY random()" not in sql


def _key(rng: random.Random, score: float) -> float:
    # Python mirror of the SQL: POWER(random(), 10000 / score^2)
    return rng.random() ** (10000.0 / max(score, 10) ** 2)


def test_simulated_bias_matches_expectation():
    # For weights w1=0.81 (score 90) and w2=0.09 (score 30), A-Res picks the
    # 90 with probability w1/(w1+w2) = 0.9.
    rng = random.Random(42)
    wins = sum(_key(rng, 90) > _key(rng, 30) for _ in range(4000))
    assert 0.86 < wins / 4000 < 0.94


def test_low_scores_still_get_sampled():
    rng = random.Random(7)
    wins = sum(_key(rng, 30) > _key(rng, 90) for _ in range(4000))
    assert wins > 100  # never starved out entirely
