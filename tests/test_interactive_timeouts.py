"""A list refresh is somebody waiting on a button, so it must fail fast.

`curl -s -X POST .../mal/<user>/refresh` printed nothing against a Jikan
that was unreachable. The endpoint handles every httpx error and always
returns a JSON body - 404, "Jikan error N", "Jikan unreachable" - so silence
could not come from the handler. It came from how long the handler took to
get there: RateLimitedClient's batch defaults are 5 retries with 2s doubling
backoff and a 30s timeout, which against a host that HANGS rather than
refuses spend 62s of backoff plus up to 6x30s of connect timeout. Four
minutes is long enough that everything in front gives up first and reports
nothing, so a blocked host reads as a silent failure.

These pin the budget and, more importantly, pin that every interactive
endpoint uses it - the next list source added here would otherwise inherit
the batch ladder by default and reintroduce the same four-minute silence.
"""

import re
from pathlib import Path

import pytest

from api.config import settings

ROUTERS = Path(__file__).resolve().parent.parent / "api" / "routers"


def worst_case_seconds(max_retries: int, timeout: float) -> float:
    """What RateLimitedClient can spend before it raises, against a hang.

    Mirrors _request_live: max_retries + 1 attempts, each able to burn the
    full timeout, with 2s doubling backoff slept between them.
    """
    attempts = max_retries + 1
    backoff = sum(2.0 * 2**i for i in range(max_retries))
    return attempts * timeout + backoff


def test_the_batch_defaults_really_are_too_slow_for_a_button():
    """The number that explains the silence; if this drops, the rest is moot."""
    assert worst_case_seconds(5, 30.0) > 200


def test_interactive_budget_answers_well_inside_a_minute():
    budget = worst_case_seconds(settings.interactive_max_retries, settings.interactive_timeout)
    assert budget <= 60, f"an interactive refresh may take {budget}s"


def test_interactive_budget_still_survives_one_blip():
    """Fast is not the only requirement: a single transient must not fail."""
    assert settings.interactive_max_retries >= 1


@pytest.mark.parametrize("router", ["mal.py", "anilist.py", "kitsu.py", "yamtrack.py"])
def test_every_interactive_router_uses_the_budget(router):
    """Drift guard against a new list source inheriting the batch ladder.

    The failure this prevents is silent and remote: an endpoint added
    without these two arguments works fine until its upstream hangs, and
    then takes four minutes to say so.
    """
    source = (ROUTERS / router).read_text()
    constructions = re.findall(r"RateLimitedClient\((.*?)\n    \)", source, re.S)
    assert constructions, f"{router} no longer constructs a client; update this test"
    for args in constructions:
        assert "max_retries=settings.interactive_max_retries" in args, router
        assert "timeout=settings.interactive_timeout" in args, router


def test_pipeline_passes_keep_the_patient_defaults():
    """The budget is for interactive endpoints only.

    An unattended backfill walking 22,000 titles SHOULD retry patiently -
    failing a three-hour pass on one blip would be worse than waiting. So
    this asserts the split is real and the pipeline was not swept up in it.
    """
    pipeline = Path(__file__).resolve().parent.parent / "pipeline"
    offenders = [
        path.name for path in pipeline.glob("*.py") if "interactive_max_retries" in path.read_text()
    ]
    assert offenders == [], offenders
