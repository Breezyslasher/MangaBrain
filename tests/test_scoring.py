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


def test_no_genres_is_undefined_not_zero():
    """Jaccard over an empty set is undefined rather than minimal, and
    the difference decides how a title ranks.

    Zero says "these share no genres", which is evidence of
    dissimilarity. An absent genre list says nothing at all. AniList
    gives almost everything genres so it never mattered; Open Library
    has no genre vocabulary, and 34% of imported books have none.
    """
    assert genre_similarity([], ["Action"]) is None
    assert genre_similarity(["Action"], []) is None
    assert genre_similarity([], []) is None


def test_an_uncomparable_genre_term_redistributes_its_weight():
    """The same fallback the taste weight already uses when no list
    source is available. Scoring it zero would cap a book with no
    genres at 0.8 of the available score - a fifth of its ranking lost
    to a gap in someone's cataloguing, which this codebase already
    refuses to do for lengths and for years."""
    from api.scoring import Weights, final_score

    weights = Weights(semantic=0.5, tags=0.3, genres=0.2)
    # A perfect match on everything comparable scores 1.0, not 0.8.
    assert final_score(1.0, 1.0, None, weights) == 1.0
    # And the surviving terms keep their ratio: 0.5 to 0.3 becomes
    # 0.625 to 0.375.
    assert abs(final_score(1.0, 0.0, None, weights) - 0.625) < 1e-9
    assert abs(final_score(0.0, 1.0, None, weights) - 0.375) < 1e-9


def test_a_genuine_genre_mismatch_still_scores_zero():
    """Undefined and disjoint are different facts and must stay so."""
    from api.scoring import Weights, final_score

    weights = Weights(semantic=0.5, tags=0.3, genres=0.2)
    assert genre_similarity(["Action"], ["Romance"]) == 0.0
    assert abs(final_score(1.0, 1.0, 0.0, weights) - 0.8) < 1e-9


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


def test_taste_weight_participates_when_set():
    w = Weights(0.5, 0.0, 0.0, 0.5)
    assert final_score(1.0, 0.0, 0.0, w, taste_sim=1.0) == 1.0
    assert final_score(1.0, 0.0, 0.0, w, taste_sim=0.0) == 0.5


def test_taste_defaults_to_off():
    # Default weights: taste contributes nothing even if a value is passed.
    assert final_score(1.0, 1.0, 1.0, Weights(), taste_sim=1.0) == final_score(
        1.0, 1.0, 1.0, Weights()
    )


def test_normalization_includes_taste():
    w = Weights(1.0, 1.0, 1.0, 1.0).normalized()
    assert w.semantic == w.tags == w.genres == w.taste == 0.25


def test_both_clients_accept_an_absent_genre_component():
    """A nullable field is only safe if its consumers know.

    Android declared `val genres: Double`, which throws on null, and
    that would have hit a third of book recommendations. The web client
    did Math.round(null * 100), which is 0 - no crash, but it displays
    "nothing in common" for a comparison that was never made.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    models = (root / "android/app/src/main/java/com/mangabrain/app/data/api/Models.kt").read_text()
    components = models[models.index("data class ScoreComponents(") :]
    components = components[: components.index(")")]
    assert "val genres: Double? = null" in components

    app = (root / "web/app.js").read_text()
    assert "components.genres === null" in app
    assert "not compared" in app


def test_the_ranking_function_itself_can_be_measured():
    """Until now nothing measured it.

    pipeline.benchmark answers "which embedding model" by raw cosine
    and never calls final_score, so every change to the weights, to tag
    similarity or to the genre term went in on argument alone. The
    score mode ranks by the real weighted score and reports an absent
    genre list treated both ways, so the change can be compared rather
    than asserted.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "pipeline" / "benchmark.py").read_text()
    body = source[source.index("def run_score_bench(") :]
    body = body[: body.index("\ndef ", 1)]
    assert "final_score(" in body
    assert "tag_similarity(" in body
    assert "genre_similarity(" in body
    # Both treatments in one run, over one pool: a before/after taken
    # from two runs would differ by the random negatives as well.
    assert '"undefined"' in body and '"zero"' in body


def test_every_benchmark_mode_ranks_over_the_same_world():
    """A second arrangement of pairs and negatives would drift from the
    first, and the numbers would stop being comparable: the model, score
    and description benchmarks all load one world."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "pipeline" / "benchmark.py").read_text()
    assert source.count("load_world(conn, pairs, negatives)") == 3


def test_the_description_benchmark_cuts_text_as_the_rules_do():
    from pipeline.benchmark import DESCRIPTION_VARIANTS, variant_text

    row = {
        "title_romaji": "T",
        "title_english": None,
        "description_clean": "D" * 500,
        "genres": ["Drama"],
        "tags": [{"name": "Lawyer", "rank": 90}],
    }
    assert "D" * 80 in variant_text(row, 80, True) and "D" * 81 not in variant_text(row, 80, True)
    assert "Lawyer" not in variant_text(row, 80, False)
    assert variant_text(row, 0, True).startswith("T"), "no description falls back to the title"
    assert {label for label, _, _ in DESCRIPTION_VARIANTS} >= {"200 chars", "80 chars"}
