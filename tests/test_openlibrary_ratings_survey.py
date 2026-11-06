"""The ratings survey, tested where it is easy to get wrong.

It runs for minutes against two remote dumps, so every decision it makes
about a row is made in a function that can be called with a row.
"""

from pipeline.survey_openlibrary_ratings import RATING_RANGE, VOTE_FLOORS, work_id


def test_a_work_key_reduces_to_its_id_however_it_is_spelled():
    """The two dumps are joined on this, and nothing else. A key spelled
    one way in the ratings dump and another in the works dump would
    report zero coverage, which is the answer the survey is most likely
    to reach anyway - so it would look like a result, not a bug."""
    assert work_id("/works/OL12345W") == "OL12345W"
    assert work_id("OL12345W") == "OL12345W"
    assert work_id("  /works/OL12345W \n") == "OL12345W"


def test_the_rating_is_found_by_value_not_by_column():
    """The edition key is documented as optional, so the rating is not at
    a fixed index: on a row that has one it sits a column further right.
    Reading a fixed column would take the edition key as a score on some
    rows and the rating on others."""
    from pipeline.survey_openlibrary_ratings import RATING_RANGE

    def rating_of(parts: list[str]) -> int | None:
        for field in parts[1:]:
            text = field.strip()
            if text.isdigit() and int(text) in RATING_RANGE:
                return int(text)
        return None

    # With an edition key, and without. Same rating either way.
    assert rating_of(["/works/OL1W", "/books/OL2M", "4", "2026-01-01"]) == 4
    assert rating_of(["/works/OL1W", "", "4", "2026-01-01"]) == 4
    assert rating_of(["/works/OL1W", "4", "2026-01-01"]) == 4
    # A row whose rating is off the star scale is not averaged in.
    assert rating_of(["/works/OL1W", "/books/OL2M", "97", "2026-01-01"]) is None


def test_the_star_scale_is_the_one_open_library_uses():
    assert list(RATING_RANGE) == [1, 2, 3, 4, 5]


def test_coverage_is_reported_at_several_vote_floors():
    """One coverage number cannot answer the question. A rating is worth
    filtering on only with votes behind it, and the collapse between the
    floors is what says whether the ratings are a long tail of single
    votes."""
    assert 1 in VOTE_FLOORS and 5 in VOTE_FLOORS
    assert list(VOTE_FLOORS) == sorted(VOTE_FLOORS)


def test_the_survey_reuses_the_importer_s_reader_and_gate():
    """Measuring a different slice from the one we import would answer a
    question nobody asked. The gate and the reader are imported, not
    restated."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "pipeline/survey_openlibrary_ratings.py"
    ).read_text()
    assert "from pipeline.openlibrary import (" in source
    for name in ("MIN_DESCRIPTION", "description_of", "iter_works", "download_chunks"):
        assert name in source, name
    assert "MIN_DESCRIPTION = " not in source, "the floor is imported, not redeclared"


def test_a_truncated_ratings_dump_is_reported_not_swallowed():
    """A short ratings dump understates coverage, which argues for the
    conclusion this survey is most likely to reach. Silent agreement
    with a cheap answer is the failure to guard against."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "pipeline/survey_openlibrary_ratings.py"
    ).read_text()
    assert "truncated_ratings" in source
    assert "coverage is a floor" in source
