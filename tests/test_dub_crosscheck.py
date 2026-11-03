"""Title matching for the ANN cross-check.

The cross-check bounds how often AniList's "no English cast" is wrong, which
is only worth anything if the title matching is right. A wrong match asserts a
dub belonging to a different show, so the rule is deliberately strict: exactly
one anime entry whose normalised title matches, or the title is reported
unresolved rather than guessed at.
"""

from xml.etree import ElementTree

from pipeline.probe_dub_crosscheck import ann_english_cast, ann_titles, normalize

SEARCH = """<ann>
  <manga id="1555"><info type="Main title">Cowboy Bebop</info></manga>
  <anime id="353">
    <info type="Main title">Cowboy Bebop: The Movie</info>
    <info type="Alternative title">Cowboy Bebop: Knockin' on Heaven's Door</info>
    <cast lang="EN"><person>Steve Blum</person></cast>
    <cast lang="JA"><person>Koichi Yamadera</person></cast>
  </anime>
  <anime id="13">
    <info type="Main title">Cowboy Bebop</info>
    <cast lang="JA"><person>Koichi Yamadera</person></cast>
  </anime>
</ann>"""


def entries(xml: str = SEARCH) -> list[ElementTree.Element]:
    return list(ElementTree.fromstring(xml).iter("anime"))


def resolve(title: str, xml: str = SEARCH) -> list[str]:
    wanted = normalize(title)
    return [a.attrib["id"] for a in entries(xml) if wanted in ann_titles(a)]


def test_normalization_matches_the_sql_rule():
    # api/routers/search.py strips punctuation and case but keeps non-ASCII.
    assert normalize("Kaguya-sama: Love Is War!") == normalize("kaguya sama love is war")
    assert normalize("かぐや様は告らせたい") == "かぐや様は告らせたい"
    assert normalize(None) == ""


def test_a_manga_of_the_same_name_is_not_an_anime_match():
    # ANN returns manga for the same search, and a manga has no dub.
    assert resolve("Cowboy Bebop") == ["13"]


def test_a_sequel_or_movie_is_not_matched_to_the_series():
    assert resolve("Cowboy Bebop: The Movie") == ["353"]
    assert "353" not in resolve("Cowboy Bebop")


def test_alternative_titles_resolve():
    assert resolve("Cowboy Bebop: Knockin' on Heaven's Door") == ["353"]


def test_a_prefix_is_not_a_match():
    assert resolve("Cowboy") == []


def test_two_entries_sharing_a_name_are_ambiguous_not_a_guess():
    xml = """<ann>
      <anime id="1"><info type="Main title">Bloom</info></anime>
      <anime id="2"><info type="Main title">Bloom</info></anime>
    </ann>"""
    assert sorted(resolve("Bloom", xml)) == ["1", "2"]


def test_english_cast_is_counted_and_languages_reported():
    movie, series = entries()
    assert ann_english_cast(movie) == (1, {"EN", "JA"})
    # Japanese-only: the whole point of the measurement.
    assert ann_english_cast(series) == (0, {"JA"})


def test_an_entry_with_no_cast_claims_nothing():
    (solo,) = entries('<ann><anime id="9"><info type="Main title">X</info></anime></ann>')
    assert ann_english_cast(solo) == (0, set())


def test_english_title_is_tried_before_romaji():
    """ANN indexes by English release title, AniList hands back romaji.

    Searching romaji only failed to match 15 of 17 titles, which reads as ANN
    not having the entries rather than the query using the wrong name: ANN
    files "Shuumatsu no Harem" as "World's End Harem".
    """
    from pipeline.probe_dub_crosscheck import candidate_titles

    media = {
        "title": {
            "english": "World's End Harem",
            "romaji": "Shuumatsu no Harem",
            "native": "終末のハーレム",
        },
        "synonyms": ["Last Man on Earth"],
    }
    assert candidate_titles(media)[0] == "World's End Harem"
    assert "Shuumatsu no Harem" in candidate_titles(media)


def test_candidates_dedupe_on_the_normalised_form():
    from pipeline.probe_dub_crosscheck import candidate_titles

    media = {
        "title": {"english": "Kuma Miko", "romaji": "kuma-miko!", "native": None},
        "synonyms": ["KUMA MIKO"],
    }
    # All three normalise identically, so ANN is asked once, not three times.
    assert len(candidate_titles(media)) == 1


def test_missing_titles_are_skipped_not_searched_as_blanks():
    from pipeline.probe_dub_crosscheck import candidate_titles

    assert candidate_titles({"title": {}, "synonyms": None}) == []
    assert candidate_titles({"title": {"english": None, "romaji": "capeta"}}) == ["capeta"]
