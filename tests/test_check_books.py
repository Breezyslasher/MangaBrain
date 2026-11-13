"""The well-known-books coverage check: title matching and the reasons it
gives for a book the import skipped."""

from pipeline.check_books import BOOKS, normalise, reason, title_keys, wanted_keys

WORK = {
    "key": "/works/OL15572356W",
    "title": "A Dog's Purpose",
    "description": "D" * 190,
    "subjects": ["Dogs", "Reincarnation", "Fiction", "Pets", "Human-animal relationships"],
}


def test_titles_meet_across_punctuation_accents_and_spacing():
    assert normalise("A Dog’s Purpose") == normalise("A Dog's Purpose")
    assert normalise("Moby-Dick") == normalise("Moby Dick")
    assert normalise("Les Misérables") == normalise("Les Miserables")


def test_a_subtitle_does_not_hide_a_book():
    assert normalise("Sapiens") in title_keys("Sapiens: A Brief History of Humankind")
    assert normalise("Moby Dick") in title_keys("Moby-Dick; or, The Whale")
    assert normalise("Dune") in title_keys("Dune (Dune Chronicles, #1)")


def test_alternative_titles_each_count():
    keys = wanted_keys("Nineteen Eighty-Four|1984")
    assert normalise("1984") in keys and normalise("Nineteen Eighty-Four") in keys


def test_reasons():
    assert reason(None) == "not on Open Library"
    assert reason(WORK).startswith("qualifies now")
    assert reason({**WORK, "description": ""}) == "no description on the work (5 real subjects)"
    assert reason({**WORK, "subjects": WORK["subjects"][:2]}) == (
        "description 190 chars, 2 real subjects"
    )
    assert reason({**WORK, "description": "D" * 40}) == "description 40 chars, 5 real subjects"


def test_the_list_is_well_formed():
    assert len(BOOKS) >= 100
    assert len({t for t, _ in BOOKS}) == len(BOOKS), "no duplicates"
    assert all(t.strip() and a.strip() for t, a in BOOKS)


def test_one_book_can_be_checked_by_title(monkeypatch):
    """--title checks the named book alone, not the whole list."""
    from pipeline import check_books

    seen = []
    monkeypatch.setattr(check_books, "check", lambda lookup, books: seen.append(books))
    monkeypatch.setattr(
        "sys.argv", ["check_books", "--title", "Death Troopers", "--author", "Joe Schreiber"]
    )
    check_books.main()
    assert seen == [[("Death Troopers", "Joe Schreiber")]]
