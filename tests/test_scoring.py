"""Guardrails for the ranking function: content similarity only, never
popularity. These tests assert both the function inputs and the module source
so an audience-size signal cannot sneak into scoring."""

import inspect

import api.scoring as scoring
from api.scoring import (
    Weights,
    display_similarity,
    final_score,
    genre_similarity,
    merge_tag_maps,
    tag_similarity,
    tag_weight_map,
)

FORBIDDEN_INPUTS = {
    "popularity",
    "favourites",
    "favorites",
    "members",
    "average_score",
    "mean_score",
    "trending",
    "rank_overall",
}

FORBIDDEN_WORDS = ("popularity", "favourites", "favorites", "members", "trending")


def test_ranking_inputs_contain_no_popularity_signals():
    for fn in (final_score, tag_similarity, genre_similarity):
        assert not set(inspect.signature(fn).parameters) & FORBIDDEN_INPUTS


def test_scoring_module_never_references_popularity():
    source = inspect.getsource(scoring)
    for word in FORBIDDEN_WORDS:
        assert word not in source, f"scoring module references {word}"


def test_hidden_gem_outranks_popular_title():
    # An obscure title with high content similarity must beat a popular title
    # with lower similarity; there is no input through which popularity could
    # change this ordering.
    weights = Weights()
    obscure = final_score(0.9, 0.8, 0.7, weights)
    popular = final_score(0.5, 0.4, 0.3, weights)
    assert obscure > popular


def test_weights_normalize():
    w = Weights(2.0, 1.0, 1.0).normalized()
    assert abs(w.semantic + w.tags + w.genres - 1.0) < 1e-9
    assert abs(w.semantic - 0.5) < 1e-9


def test_zero_weights_fall_back_to_defaults():
    w = Weights(0.0, 0.0, 0.0).normalized()
    assert (w.semantic, w.tags, w.genres) == (0.5, 0.3, 0.2)


def test_default_weights():
    w = Weights()
    assert (w.semantic, w.tags, w.genres) == (0.5, 0.3, 0.2)


def test_genre_similarity_jaccard():
    assert genre_similarity(["Action", "Drama"], ["Action", "Drama"]) == 1.0
    assert genre_similarity(["Action", "Drama"], ["Action", "Comedy"]) == 1 / 3
    assert genre_similarity([], ["Action"]) == 0.0
    assert genre_similarity(["Action"], []) == 0.0


def test_tag_similarity_cosine():
    a = {"Time Travel": 0.9, "Tragedy": 0.8}
    assert abs(tag_similarity(a, a) - 1.0) < 1e-9
    assert tag_similarity(a, {"Sports": 0.9}) == 0.0
    assert tag_similarity(a, {}) == 0.0
    partial = tag_similarity(a, {"Time Travel": 0.9})
    assert 0.0 < partial < 1.0


def test_tag_weight_map_scales_rank_and_defaults():
    tags = [
        {"name": "Time Travel", "rank": 90},
        {"name": "Tragedy", "rank": None},
        {"name": None, "rank": 50},
    ]
    weights = tag_weight_map(tags)
    assert weights == {"Time Travel": 0.9, "Tragedy": 0.5}


def test_final_score_clamps_semantic():
    assert final_score(1.5, 0.0, 0.0, Weights(1.0, 0.0, 0.0)) == 1.0
    assert final_score(-0.5, 0.0, 0.0, Weights(1.0, 0.0, 0.0)) == 0.0


def test_display_similarity_is_monotonic_and_bounded():
    # Calibration must never change ranking order, only the displayed scale.
    scores = [i / 100 for i in range(101)]
    displayed = [display_similarity(s) for s in scores]
    assert displayed == sorted(displayed)
    assert displayed[0] == 0.0
    assert displayed[-1] == 100.0
    # A strong blended score reads Anibrain-high.
    assert display_similarity(0.75) > 90.0


def test_merge_tag_maps_averages_profiles():
    merged = merge_tag_maps([{"a": 0.8, "b": 0.4}, {"a": 0.4}])
    assert merged == {"a": 0.6000000000000001, "b": 0.2} or (
        abs(merged["a"] - 0.6) < 1e-9 and abs(merged["b"] - 0.2) < 1e-9
    )
    assert merge_tag_maps([]) == {}
