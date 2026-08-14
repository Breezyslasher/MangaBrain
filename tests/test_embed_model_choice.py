"""Seed embedding-version selection during re-embed migrations.

The seeds join can return vectors from two embedding spaces mid-migration;
selection must be deterministic and must prefer the version with the most
catalog coverage (the candidate pool is limited to entries embedded with the
chosen version), not whichever row the database returned last.
"""

from api.routers.recommend import _choose_embed_model

OLD = "all-MiniLM-L6-v2#text-v3"
NEW = "BAAI/bge-small-en-v1.5#text-v3"


def test_single_shared_model_is_chosen_without_counts():
    assert _choose_embed_model([{OLD}, {OLD}], {}, preferred=NEW) == OLD


def test_coverage_wins_mid_migration():
    # Seed already re-embedded, but the new model covers 3% of the catalog:
    # scoring in the new space would shrink the candidate pool to that 3%.
    counts = {OLD: 160_000, NEW: 5_000}
    assert _choose_embed_model([{OLD, NEW}], counts, preferred=NEW) == OLD


def test_configured_model_wins_once_dominant():
    counts = {OLD: 20_000, NEW: 160_000}
    assert _choose_embed_model([{OLD, NEW}], counts, preferred=NEW) == NEW


def test_tie_breaks_to_configured_model():
    counts = {OLD: 100, NEW: 100}
    assert _choose_embed_model([{OLD, NEW}], counts, preferred=NEW) == NEW


def test_stragglers_do_not_pin_the_old_space_forever():
    # A finished migration with a few permanently failed re-embeds: the
    # configured model is within 1% of full coverage and must win.
    counts = {OLD: 160_820, NEW: 160_800}
    assert _choose_embed_model([{OLD, NEW}], counts, preferred=NEW) == NEW


def test_no_common_version_returns_none():
    assert _choose_embed_model([{OLD}, {NEW}], {}, preferred=NEW) is None


def test_common_subset_across_seeds():
    # One seed re-embedded, the other not: the shared old version is the
    # only consistent space, regardless of coverage.
    counts = {OLD: 80_000, NEW: 80_001}
    assert _choose_embed_model([{OLD, NEW}, {OLD}], counts, preferred=NEW) == OLD
