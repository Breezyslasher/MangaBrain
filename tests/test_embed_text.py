from pipeline.embed import MAX_TAGS, MAX_TEXT_CHARS, embed_text


def test_includes_genres_and_themes():
    text = embed_text(
        "DEATH NOTE",
        "Death Note",
        "A notebook that kills.",
        genres=["Mystery", "Psychological", "Thriller"],
        tags=[{"name": "Serial Killers", "rank": 90}, {"name": "Detective", "rank": 80}],
    )
    assert "Genres: Mystery, Psychological, Thriller" in text
    assert "Themes: Serial Killers, Detective" in text
    assert text.endswith("A notebook that kills.")


def test_tags_sorted_by_rank_and_capped():
    tags = [{"name": f"Tag{i}", "rank": i} for i in range(1, 31)]
    text = embed_text("Title", None, None, tags=tags)
    themes = next(line for line in text.split("\n") if line.startswith("Themes:"))
    names = themes.removeprefix("Themes: ").split(", ")
    assert len(names) == MAX_TAGS
    assert names[0] == "Tag30"
    assert "Tag1" not in names


def test_compact_signals_survive_truncation():
    # A huge synopsis must not push genres/themes past the cutoff: they come
    # before the description, so truncation only cuts the synopsis tail.
    text = embed_text("Title", None, "x" * 10000, genres=["Action"], tags=[])
    assert len(text) == MAX_TEXT_CHARS
    assert "Genres: Action" in text


def test_handles_missing_fields():
    assert embed_text(None, None, None) == ""
    assert embed_text("Title", None, None, genres=[], tags=[]) == "Title"
    assert embed_text("Title", None, None, tags=[{"name": None, "rank": 50}]) == "Title"
