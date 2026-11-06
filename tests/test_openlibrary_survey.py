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


def test_a_chunked_stream_reads_as_a_file():
    """gzip.GzipFile wants .read(); httpx yields chunks.

    Passing the iterator straight in raised "'generator' object has no
    attribute 'read'" on the first byte of a multi-gigabyte download.
    Driving real gzip bytes through the adapter in awkward chunk sizes
    is cheap and catches it here instead of in CI.
    """
    import gzip
    import io

    from pipeline.survey_openlibrary import ChunkReader

    payload = b"".join(f"line {n}\n".encode() for n in range(500))
    compressed = gzip.compress(payload)
    # Deliberately uneven, and smaller than any gzip header or member.
    chunks = [compressed[i : i + 7] for i in range(0, len(compressed), 7)]

    reader = io.BufferedReader(ChunkReader(chunks))
    with gzip.GzipFile(fileobj=reader, mode="rb") as stream:
        assert stream.read() == payload


def test_the_adapter_yields_lines_in_order():
    """The survey iterates the stream line by line, so the adapter has
    to behave for readline() too, not only read()."""
    import gzip
    import io

    from pipeline.survey_openlibrary import ChunkReader

    payload = b"first\tsecond\n" + b"third\tfourth\n"
    chunks = [gzip.compress(payload)[i : i + 3] for i in range(0, len(gzip.compress(payload)), 3)]
    reader = io.BufferedReader(ChunkReader(chunks))
    with gzip.GzipFile(fileobj=reader, mode="rb") as stream:
        assert [line for line in stream] == [b"first\tsecond\n", b"third\tfourth\n"]


def test_an_empty_stream_ends_rather_than_hangs():
    from pipeline.survey_openlibrary import ChunkReader

    assert ChunkReader([]).readinto(bytearray(8)) == 0


def test_one_pass_answers_every_threshold():
    """A full survey is ~2.6 hours, so re-running it per candidate floor
    is not a way to choose one.

    200 characters was picked to exclude taglines, not to select good
    records, and at 2.32% of a ~40M-work dump the difference is between
    roughly 930,000 books and a few hundred thousand - which decides
    embedding hours, how many release assets it takes to ship, and what
    competes for a place in the results.
    """
    from pipeline.survey_openlibrary import THRESHOLDS

    assert (200, 1) in THRESHOLDS, "the current floor must stay comparable"
    assert len({chars for chars, _ in THRESHOLDS}) >= 3, "several description floors"
    assert len({tags for _, tags in THRESHOLDS}) >= 2, "and more than one subject floor"


def test_each_threshold_reports_its_embedding_cost():
    """The count alone does not say what it would cost to act on."""
    source = SURVEY.read_text()
    assert "h to embed" in source
    assert "165_000" in source, "scaled from the rate this project actually measured"


def test_the_grid_counts_subjects_not_merely_their_presence():
    """The usable count asks only whether subjects exist; a floor of 3
    needs the number."""
    source = SURVEY.read_text()
    assert "len(subjects) >= tags" in source
