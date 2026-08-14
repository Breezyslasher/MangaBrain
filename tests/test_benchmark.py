"""Metric math for the AniList recommendation-pair benchmark."""

import numpy as np

from pipeline.benchmark import rank_of_target, summarize


def test_rank_counts_only_strictly_better_scores():
    scores = np.array([0.9, 0.7, 0.8, 0.6])
    # target idx 2 (0.8): only 0.9 beats it -> rank 2
    assert rank_of_target(scores, 2, set()) == 2


def test_excluded_rows_leave_the_pool():
    scores = np.array([0.9, 0.7, 0.8, 0.6])
    # exclude the 0.9 row (the seed): target 0.8 becomes rank 1
    assert rank_of_target(scores, 2, {0}) == 1


def test_excluded_target_returns_none():
    scores = np.array([0.9, 0.7])
    assert rank_of_target(scores, 1, {1}) is None


def test_best_score_is_rank_one():
    scores = np.array([0.5, 0.99, 0.7])
    assert rank_of_target(scores, 1, set()) == 1


def test_summarize_metrics():
    m = summarize([1, 5, 20, 100])
    assert m["n"] == 4
    assert m["recall@10"] == 0.5
    assert m["recall@50"] == 0.75
    assert abs(m["mrr"] - (1 + 1 / 5 + 1 / 20 + 1 / 100) / 4) < 1e-9


def test_summarize_empty():
    assert summarize([]) == {"n": 0, "recall@10": 0.0, "recall@50": 0.0, "mrr": 0.0}
