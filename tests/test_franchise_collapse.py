"""Result-side franchise collapse: one entry per franchise in the results.

Motivated by measured eval output where three seasons of one series occupied
three top-10 slots. Edges may point at connector entries that are not
themselves candidates (two seasons often relate only through their shared
parent), so the union-find must group through off-list nodes.
"""

from api.routers.recommend import collapse_franchises


def test_direct_pair_keeps_best_ranked():
    # 10 outranks 20; they are directly related.
    assert collapse_franchises([10, 15, 20], [(10, 20)]) == [10, 15]


def test_seasons_connect_through_offlist_parent():
    # Candidates 2 and 3 are seasons of parent 1 (not a candidate itself):
    # each edge only points at the parent, yet they must still collapse.
    assert collapse_franchises([2, 5, 3], [(2, 1), (3, 1)]) == [2, 5]


def test_unrelated_titles_all_survive():
    assert collapse_franchises([1, 2, 3], []) == [1, 2, 3]


def test_chain_collapses_transitively():
    # 4-3, 3-2: one component even without a direct 4-2 edge.
    assert collapse_franchises([4, 2, 9], [(4, 3), (3, 2)]) == [4, 9]


def test_order_is_preserved():
    ordered = [7, 1, 9, 3]
    assert collapse_franchises(ordered, [(9, 7)]) == [7, 1, 3]
