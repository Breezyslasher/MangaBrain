"""Turning an Open Library work into something this engine can rank.

The scoring reads three things: the embedded description, the tags and
the genres. Open Library supplies the first two directly and has no
notion of the third, so the mapping is where books either get a fair
hearing against anime or quietly score lower for structural reasons.
"""

from pathlib import Path

from pipeline.sync_openlibrary import (
    MAX_DESCRIPTION,
    SUBJECT_RANK,
    clean_description,
    covers_of,
    row_for,
    year_of,
)

SYNC = Path(__file__).resolve().parent.parent / "pipeline" / "sync_openlibrary.py"

WORK = {
    "key": "/works/OL45804W",
    "title": "Fantastic Mr Fox",
    "description": "A" * 300,
    "subjects": ["Fiction", "Foxes", "Juvenile fiction", "Farmers"],
    "covers": [8739161],
    "first_publish_date": "October 1970",
}


def test_a_work_becomes_a_rankable_row():
    row = row_for(WORK, 200)
    assert row is not None
    assert row["source"] == "openlibrary"
    assert row["source_id"] == "/works/OL45804W"
    assert row["title"] == "Fantastic Mr Fox"
    assert row["start_year"] == 1970


def test_the_work_key_is_kept_whole():
    """ "/works/OL45804W" is what Open Library calls this and what a
    reader can look up. A parsed-out number would be ours alone."""
    assert row_for(WORK, 200)["source_id"].startswith("/works/")


def test_genre_like_subjects_become_genres_and_the_rest_tags():
    """Open Library has no genre vocabulary, so without this every book
    scores zero on one of the three terms - not because it lacks
    genres, but because the source files them under the same heading as
    "Cats in fiction, juvenile"."""
    row = row_for(WORK, 200)
    assert "Fiction" in row["genres"]
    assert "Children" in row["genres"], "juvenile fiction is a genre by another name"
    names = [t["name"] for t in row["tags"]]
    assert "Foxes" in names and "Farmers" in names
    assert "Fiction" not in names, "a subject counted as a genre is not also a tag"


def test_subjects_carry_a_uniform_rank():
    """AniList ranks tags 0-100 by fit and tag_similarity reads that.
    Open Library subjects are an unordered set, so a uniform rank says
    "present, weight unknown" instead of inventing an order."""
    row = row_for(WORK, 200)
    assert {t["rank"] for t in row["tags"]} == {SUBJECT_RANK}


def test_a_work_with_too_little_to_rank_is_skipped():
    assert row_for({**WORK, "description": "short"}, 200) is None
    assert row_for({**WORK, "subjects": []}, 200) is None
    assert row_for({**WORK, "title": ""}, 200) is None
    assert row_for({**WORK, "key": ""}, 200) is None


def test_the_provenance_footer_is_not_embedded():
    """Many descriptions end with where the blurb came from. Embedding
    that matches books by their publisher's boilerplate."""
    assert (
        clean_description("A real summary.\n----------\nSource: a publisher") == "A real summary."
    )
    assert clean_description("A real summary. ([source][1])\n[1]: http://x") == "A real summary."


def test_a_description_is_capped_before_embedding():
    """Publisher copy and review quotes run to thousands of characters
    and dilute the vector rather than sharpening it."""
    row = row_for({**WORK, "description": "B" * 10_000}, 200)
    assert len(row["description_clean"]) == MAX_DESCRIPTION
    assert len(row["description"]) == 10_000, "the full text is still stored"


def test_a_year_is_taken_only_when_it_is_really_there():
    """Works carry no reliable date - publication belongs to editions -
    and start_year drives the year filter, so a wrong year is worse
    than no year."""
    assert year_of({"first_publish_date": "October 1970"}) == 1970
    assert year_of({"first_publish_date": "1970-10-01"}) == 1970
    assert year_of({}) is None
    assert year_of({"first_publish_date": "n.d."}) is None


def test_covers_are_built_from_the_first_id():
    medium, large = covers_of(WORK)
    assert medium.endswith("-M.jpg") and large.endswith("-L.jpg")
    assert covers_of({"covers": [-1]}) == (None, None)
    assert covers_of({}) == (None, None)


def test_a_rerun_corrects_rather_than_duplicates():
    """The dump is read from the start every time, so the upsert is
    what makes a second pass safe."""
    source = SYNC.read_text()
    assert "ON CONFLICT (source, source_id) DO UPDATE" in source
    assert "nextval('media_external_id_seq')" in source


def test_nothing_is_filtered_on_popularity():
    """The floor is about having content to rank, not about being
    well-known - the same rule the scoring function follows."""
    source = SYNC.read_text()
    # Field access, not prose: the docstring says popularity is not used,
    # and a substring check cannot tell the two apart.
    for banned in ("edition_count", "want_to_read", "popularity", "ratings", "readinglog"):
        assert f'work.get("{banned}")' not in source, banned
        assert f'"{banned}"]' not in source, banned


def test_popularity_never_reaches_the_tag_vector():
    """The one inviolable rule in this project, and Open Library files
    bestseller status as a subject.

    Measured on the first 5,327 books: "New York Times bestseller"
    appeared 70 times and "New York Times reviewed" 75. Subjects become
    tags, tags feed tag_similarity, so letting these through would put
    popularity into the ranking function by the side door - two books
    scoring as alike for having sold well.
    """
    row = row_for({**WORK, "subjects": ["New York Times bestseller", "Foxes"]}, 200)
    assert [t["name"] for t in row["tags"]] == ["Foxes"]


def test_bibliographic_form_headings_are_not_content():
    """Two books share nothing by both being large-print, or both being
    conference proceedings. Tag similarity would score them as if they
    did."""
    row = row_for({**WORK, "subjects": ["Large type books", "Congresses", "Dragons"]}, 200)
    assert [t["name"] for t in row["tags"]] == ["Dragons"]


def test_a_subject_that_is_a_genre_never_becomes_a_tag():
    """Measured: "Children's fiction" landed in tags 141 times across
    5,327 books, because "Juvenile fiction" reached the same genre
    first and the duplicate fell through to the tag branch."""
    row = row_for({**WORK, "subjects": ["Juvenile fiction", "Children's fiction", "Foxes"]}, 200)
    assert row["genres"] == ["Children"]
    assert [t["name"] for t in row["tags"]] == ["Foxes"]


def test_the_bisac_shape_is_read_rather_than_enumerated():
    """Open Library files genres as "Fiction, romance, general". Listing
    every spelling would be endless; reading the shape is not."""
    from pipeline.sync_openlibrary import genre_for

    assert genre_for("fiction, romance, general") == "Romance"
    assert genre_for("fiction, historical, general") == "Historical"
    assert genre_for("fiction, general") == "Fiction"
    # A pair names the genre and its near neighbour; the first is the
    # heading it is filed under.
    assert genre_for("fiction, mystery & detective, general") == "Mystery"
    assert genre_for("fiction, science fiction & fantasy, general") == "Science Fiction"
    # And a subject that is not a genre stays a tag.
    assert genre_for("social aspects") is None


def test_bisac_top_level_categories_are_mapped_completely():
    """Open Library carries BISAC's top-level headings verbatim, in
    capitals. Unlike the long tail of subjects they are a closed
    vocabulary, so they can be mapped completely rather than guessed
    at - "BUSINESS & ECONOMICS" appeared 25 times and "SOCIAL SCIENCE"
    19 among books that had ended up with no genre at all."""
    from pipeline.sync_openlibrary import genre_for

    assert genre_for("business & economics") == "Business"
    assert genre_for("social science") == "Social Science"
    assert genre_for("comics & graphic novels") == "Comics"


def test_a_cataloguers_gloss_is_not_part_of_the_name():
    """ "Poetry (poetic works by one author)" is the heading Poetry."""
    from pipeline.sync_openlibrary import genre_for

    assert genre_for("poetry (poetic works by one author)") == "Poetry"


def test_marc_subdivisions_are_dropped_rather_than_tagged():
    """They say what the record is ABOUT its subject, not what the book
    is. Among genre-less books they were the whole top of the list:
    "History and criticism" 84, "Criticism and interpretation" 59,
    "Social aspects" 49. As tags they would make two unrelated academic
    books look alike."""
    row = row_for(
        {**WORK, "subjects": ["History and criticism", "Study and teaching", "Foxes"]}, 200
    )
    assert [t["name"] for t in row["tags"]] == ["Foxes"]


def test_a_book_with_no_genre_is_still_imported():
    """Plenty of books genuinely have no genre in any vocabulary, and
    the measured top of the genre-less list is MARC subdivisions rather
    than missed spellings. Dropping them would discard real books for a
    gap in Open Library's cataloguing."""
    row = row_for({**WORK, "subjects": ["Plant chemistry", "Insect-plant relationships"]}, 200)
    assert row is not None
    assert row["genres"] == []
    assert len(row["tags"]) == 2


def test_a_plural_heading_resolves_to_its_singular():
    """Open Library pluralises some headings and not others -
    "Fiction, thrillers, general" appeared 39 times against a map that
    knew only "thriller". One rule beats listing both spellings of
    everything, which would stay half done."""
    from pipeline.sync_openlibrary import genre_for

    assert genre_for("thrillers") == "Thriller"
    assert genre_for("fiction, thrillers, general") == "Thriller"


def test_the_plural_fallback_runs_after_the_bisac_shape():
    """Order matters and got this wrong once: a plural check that
    returned early stopped "Fiction, historical, general" from ever
    reaching the wrapper rule, silently un-mapping headings that had
    been working."""
    from pipeline.sync_openlibrary import genre_for

    assert genre_for("fiction, historical, general") == "Historical"
    assert genre_for("fiction, general") == "Fiction"
    assert genre_for("fiction, mystery & detective, general") == "Mystery"
