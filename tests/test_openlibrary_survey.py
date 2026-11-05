"""Counting the rankable part of Open Library before importing any of it.

The source probe measured 96% of sampled works carrying a description,
but that sample came from a subject:fiction search, which returns
well-populated records by construction. Open Library as a whole is
mostly bare catalogue rows, and a work with no description is embedded
from its own title - the failure that put "Liar" and "Ghost Writer" in
the results for "unreliable narrator".
"""

from pathlib import Path

SURVEY = Path(__file__).resolve().parent.parent / "pipeline" / "survey_openlibrary.py"


def test_a_description_can_be_a_string_or_a_wrapped_value():
    """Open Library stores it both ways, and reading only one shape
    would undercount the usable catalogue."""
    from pipeline.survey_openlibrary import description_of

    assert description_of({"description": "plain"}) == "plain"
    assert description_of({"description": {"value": "wrapped"}}) == "wrapped"
    assert description_of({}) == ""
    assert description_of({"description": None}) == ""


def test_the_floor_matches_the_source_probe():
    """Two tools measuring the same thing must not use two thresholds."""
    from pipeline.probe_media_sources import MIN_SYNOPSIS
    from pipeline.survey_openlibrary import MIN_DESCRIPTION

    assert MIN_DESCRIPTION == MIN_SYNOPSIS


def test_both_signals_are_required_for_usable():
    """The score is a weighted sum of semantic, tag and genre
    similarity. A work with prose and no subjects fills one term of
    three, so counting it as usable would overstate the catalogue."""
    source = SURVEY.read_text()
    assert "if len(description) >= min_description and subjects:" in source


def test_the_dump_is_streamed_rather_than_downloaded():
    """It is tens of gigabytes; a NAS should not need to land it on disk
    to answer how many rows are worth keeping."""
    source = SURVEY.read_text()
    assert "httpx.stream(" in source
    assert "gzip.GzipFile(" in source


def test_the_search_api_is_not_used_for_bulk():
    """Open Library asks bulk users to take the dumps, and at tens of
    millions of works paginating would be dishonest as well as slow."""
    source = SURVEY.read_text()
    assert "search.json" not in source
    assert "ol_dump_works" in source


def test_a_partial_survey_says_it_is_partial():
    """The dump is ordered by key, so the head skews to older records.
    A limited run is an estimate and the module has to say so."""
    source = SURVEY.read_text()
    assert "skews" in source or "estimate" in source
