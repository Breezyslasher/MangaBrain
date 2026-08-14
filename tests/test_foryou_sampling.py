"""Rating-weighted seed sampling for the For-you feed.

The SQL implements Efraimidis-Spirakis A-Res: key = u^(1/w) per row, top-n
keys win. The weight comes from each entry's percentile among the user's
own rated entries (mapped to an effective score of 40-100, unrated neutral
at 70), so every user gets the same effective spread regardless of whether
they rate 70-90 or 1-10. These tests pin the SQL shape and verify the math
produces the intended selection bias when simulated in Python.
"""

import random

from api.routers.foryou import (
    ANILIST_SEEDS_SQL,
    LIST_SEEDS_SQL,
    MAL_SEEDS_SQL,
    weighted_order,
)


def test_weighted_order_uses_percentile_with_floor():
    clause = weighted_order("r.pct")
    assert "POWER(random(), 10000.0" in clause
    # effective score = 40 + 60 * percentile; NULL (unrated) sits mid-range
    assert "40 + 60 * COALESCE(r.pct, 0.5)" in clause


def test_all_three_seed_sources_rank_within_the_users_own_list():
    for template in (ANILIST_SEEDS_SQL, MAL_SEEDS_SQL, LIST_SEEDS_SQL):
        assert "PERCENT_RANK() OVER (ORDER BY score)" in template
        assert "WHERE score IS NOT NULL" in template  # unrated stay out of ranking
        weighted = template.format(order=weighted_order("r.pct"))
        assert weighted_order("r.pct") in weighted
        assert "ORDER BY random()\n" not in weighted


def test_use_ratings_off_gives_uniform_sampling():
    # The use_ratings=false opt-out: plain ORDER BY random(), no weights.
    for template in (ANILIST_SEEDS_SQL, MAL_SEEDS_SQL, LIST_SEEDS_SQL):
        uniform = template.format(order="ORDER BY random()")
        assert "ORDER BY random()" in uniform
        assert "POWER(random()" not in uniform


def _key(rng: random.Random, pct: float) -> float:
    # Python mirror of the SQL: POWER(random(), 10000 / (40 + 60*pct)^2)
    eff = 40.0 + 60.0 * pct
    return rng.random() ** (10000.0 / eff**2)


def test_simulated_bias_matches_expectation():
    # Top-percentile weight (eff 100) is 1.0; bottom (eff 40) is 0.16.
    # A-Res picks the top with probability 1.0/(1.0+0.16) = 0.862.
    rng = random.Random(42)
    wins = sum(_key(rng, 1.0) > _key(rng, 0.0) for _ in range(4000))
    assert 0.82 < wins / 4000 < 0.90


def test_bottom_percentile_still_gets_sampled():
    rng = random.Random(7)
    wins = sum(_key(rng, 0.0) > _key(rng, 1.0) for _ in range(4000))
    assert wins > 200  # the floor keeps low-rated titles alive
