"""The nightly pass keeps all three dub columns current, independently.

The language walk was built, measured and documented but never wired into
nightly, so the 29 languages in the catalog came entirely from one manual
run and every title synced afterwards would have stayed blank. The data
would have degraded quietly as the catalog grew, which is the worst shape
for this kind of gap: nothing fails, the numbers just stop improving.

Three steps, three guards. They answer different questions and fail
independently, and none of them may cost the pass its sync or its
embeddings - dub data is the freshness of a filter, not of the catalog.
"""

import inspect

import pipeline.nightly as nightly
from api.config import settings


def dub_pass_source() -> str:
    return inspect.getsource(nightly._dub_pass)


def test_all_three_passes_run():
    source = dub_pass_source()
    assert "backfill_dub(" in source
    assert "backfill_dub_languages(" in source
    assert "propagate_dub(" in source


def test_each_pass_is_guarded_separately():
    """One failure must not cost the others their slice."""
    assert dub_pass_source().count("try:") == 3


def test_the_language_pass_is_not_nested_under_the_english_budget():
    """Setting dub_backfill_per_pass to 0 disabled everything after it."""
    source = dub_pass_source()
    assert "if settings.dub_backfill_per_pass > 0:" in source
    assert "if settings.dub_language_per_pass > 0:" in source
    # No early return, which is what made the budgets interdependent.
    assert "return\n" not in source.split("try:")[0]


def test_each_budget_can_be_disabled_on_its_own():
    assert settings.dub_backfill_per_pass >= 0
    assert settings.dub_language_per_pass >= 0


def test_the_language_budget_is_larger_than_the_english_one():
    """Both fetch fifty titles per request, so the same request cost buys
    more titles; the language pass starts from an empty column."""
    assert settings.dub_language_per_pass > settings.dub_backfill_per_pass


def test_propagation_runs_after_both_walks():
    """It should see whatever either walk just wrote, on the same pass."""
    source = dub_pass_source()
    assert source.index("backfill_dub(") < source.index("propagate_dub(")
    assert source.index("backfill_dub_languages(") < source.index("propagate_dub(")


def test_propagation_roots_on_every_direct_source():
    """measured_dub_languages was missing, so a title whose only English
    evidence lived there could not seed its franchise."""
    from pipeline.propagate_dub import REACHABLE_SQL

    # Not split on UNION: the edges CTE has one of its own, to make the
    # relation graph symmetric.
    seed = REACHABLE_SQL[REACHABLE_SQL.index("walk (node, depth)") :]
    seed = seed[: seed.index("UNION")]
    assert "english_va_count > 0" in seed
    assert "'en' = ANY(m.dub_languages)" in seed
    assert "'en' = ANY(m.measured_dub_languages)" in seed


def test_a_measured_dub_is_never_relabelled_as_inferred():
    """The worse half of the same omission: a title measured only in
    measured_dub_languages was eligible to be marked english_dub_inferred,
    downgrading a measurement to an inference."""
    from pipeline.propagate_dub import MARK_SQL, REACHABLE_SQL

    for sql in (MARK_SQL, REACHABLE_SQL):
        assert "measured_dub_languages" in sql
        assert (
            "NOT ('en' = ANY(measured_dub_languages))" in sql
            or "NOT ('en' = ANY(m.measured_dub_languages))" in sql
        )
