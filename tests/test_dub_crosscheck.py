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
