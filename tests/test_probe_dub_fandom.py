"""Parsing and matching for the Dubbing Wiki probe.

Everything here runs on fixtures. The wiki's real page format has not been
inspected yet - that is what the recon subcommand is for - so these pin the
mechanics (what a heading is, what a confirmed match is) rather than
claiming to know the format.
"""

from pipeline.probe_dub_fandom import (
    confirm,
    english_cast,
    headings,
    section,
    templates,
)

PAGE = """{{Infobox dub
|title = Example Show
|country = Japan
}}
Some prose about the show.

==English==
===Cast===
{| class="wikitable"
! Character !! Actor
|-
| Hero || [[Jane Doe]]
|-
| Rival || [[John Roe|John]]
|}
See also [[Category:Anime]] and [[File:Poster.jpg]].

==Spanish==
| Hero || [[Alguien Mas]]

==External links==
"""


def test_headings_are_found_at_every_level():
    assert headings(PAGE) == ["English", "Cast", "Spanish", "External links"]


def test_templates_are_counted():
    assert templates(PAGE)["Infobox dub"] == 1


def test_a_section_stops_at_the_next_heading_of_its_level():
    """A subsection belongs to its parent; the next language does not."""
    english = section(PAGE, "english")
    assert "Jane Doe" in english
    assert "Cast" in english, "a deeper subsection stays inside"
    assert "Alguien Mas" not in english, "the next language must not bleed in"


def test_a_missing_section_is_empty_not_an_error():
    assert section(PAGE, "korean") == ""


def test_cast_links_exclude_files_and_categories():
    """Wiki links point at more than people."""
    cast = english_cast(PAGE)
    assert cast == ["Jane Doe", "John Roe"]


def test_a_page_with_no_english_section_yields_no_cast():
    assert english_cast("==Spanish==\n[[Alguien Mas]]") == []


def test_only_a_normalised_title_match_is_accepted():
    """Search returns anything sharing a word, and accepting the first hit
    is how a title-matched source asserts another show's dub."""
    assert confirm("Cowboy Bebop", "Cowboy Bebop")
    assert confirm("The Amazing 3", "the amazing 3")
    assert not confirm("Discode", "Discode (disambiguation)")
    assert not confirm("Monster", "Monster Rancher")
