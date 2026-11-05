"""Measuring whether another medium is rankable at all.

The engine reads a synopsis, tags and a stable id per entry, and nothing
else. So the question that decides whether books or games can be ranked
is not catalogue size or API polish - it is what share of entries carry
a description worth embedding. A title with no synopsis is embedded from
its own name, which is how "Liar" and "Ghost Writer" surfaced for
"unreliable narrator".

These tests pin the shape of that measurement, not its result.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "pipeline" / "probe_media_sources.py"
WORKFLOW = ROOT / ".github" / "workflows" / "media-source-probe.yml"


def test_the_threshold_is_a_synopsis_not_a_tagline():
    from pipeline.probe_media_sources import MIN_SYNOPSIS

    assert MIN_SYNOPSIS >= 200, "a one-line blurb cannot carry content similarity"


def test_every_domain_the_user_asked_about_is_covered():
    """Books, games board and video, screen, comics."""
    from pipeline.probe_media_sources import PROBES

    assert set(PROBES) == {"books", "boardgames", "videogames", "screen", "comics"}


def test_a_key_gated_api_is_measured_rather_than_assumed():
    """Documentation says what an API offers; a request says what it
    gives you without a key. Three sources in this project turned out to
    behave differently from their docs - Jikan's user endpoints, MAL's
    silent field-ignoring, ANN's pt-br - so the gate is measured."""
    from pipeline.probe_media_sources import probe_keyless_refusal

    assert probe_keyless_refusal.__doc__
    source = PROBE.read_text()
    assert "probe_keyless_refusal(" in source
    assert "401, 403" in source


def test_the_probe_carries_no_credential():
    """It runs in a public workflow log, and none of it needs a key."""
    import re

    source = PROBE.read_text()
    assert "api_key" not in source.lower().replace("api_key=", "api_key=")  # no literal
    assert not re.search(r"\b[0-9a-f]{32}\b", source)
    workflow = WORKFLOW.read_text()
    assert "secrets." not in workflow, "this probe deliberately uses no credential"


def test_the_sample_is_small_enough_to_be_polite():
    """These are other people's free APIs, several without keys."""
    from pipeline.probe_media_sources import SAMPLE

    assert SAMPLE <= 50


def test_the_workflow_offers_each_domain_separately():
    """So one slow or rate-limited source does not cost the others."""
    workflow = WORKFLOW.read_text()
    for domain in ("books", "boardgames", "videogames", "screen", "comics", "all"):
        assert f"- {domain}" in workflow, domain


def test_tags_and_synopsis_are_both_reported():
    """Either alone is not enough: the score is a weighted sum of
    semantic, tag and genre similarity, so a source with prose and no
    tags only fills one of three terms."""
    source = PROBE.read_text()
    assert "synopsis_lengths" in source
    assert "tag_counts" in source
