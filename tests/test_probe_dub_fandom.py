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


REAL_SHAPE = """{{Infobox
|title = Example
}}
==Dubbing History==
Some prose.
==Cast==
{| class="wikitable"
! Character !! {{Flag|United States}} English !! {{Flag|Brazil}} Portuguese
|-
| Hero || [[Jane Doe]] || [[Alguem]]
|}
==External Links==
{{IMDb title|tt0000001}}
{{ANN title|anime|1234}}
{{AniList title|5678}}
[[Category:Anime]]
"""


def test_the_real_page_shape_has_no_english_section():
    """What the first recon run actually found, pinned as a regression.

    Cowboy Bebop and The Amazing 3 both have headings Dubbing History /
    Cast / Additional Voices and NO per-language sections: languages are
    columns in a wikitable, marked with {{Flag}}. The provisional
    english_cast() guess therefore returns nothing on a real page, which
    is why its output was labelled an upper bound rather than a finding.
    """
    from pipeline.probe_dub_fandom import english_cast, headings

    assert "English" not in headings(REAL_SHAPE)
    assert english_cast(REAL_SHAPE) == [], "the guess does not fit the real format"
    assert section(REAL_SHAPE, "cast") != "", "the cast is under a Cast heading"


def test_cross_reference_templates_are_captured_verbatim():
    """The AniList template is the find: a page carrying it can be joined
    by id, which removes the title matching that this source was assumed
    to be stuck with."""
    from pipeline.probe_dub_fandom import template_calls

    calls = template_calls(REAL_SHAPE)
    assert "{{AniList title|5678}}" in calls
    assert "{{ANN title|anime|1234}}" in calls
    # Flag and Infobox are not cross-reference templates and must not be
    # dragged in by a loose match.
    assert not any("Flag" in call or "Infobox" in call for call in calls)


def test_the_anilist_id_is_read_from_the_shape_the_wiki_uses():
    """{{AniList title|#=1}} - a named parameter called "#".

    Read off a real page rather than assumed, and verifiable: Cowboy
    Bebop's value is 1, which is its actual AniList id.
    """
    from pipeline.probe_dub_fandom import anilist_id

    assert anilist_id("{{AniList title|#=1}}") == 1
    assert anilist_id("{{AniList title | # = 20661 }}") == 20661
    # Another site's id must never be read as an AniList one.
    assert anilist_id("{{ANN title|#=13}}") is None
    assert anilist_id("{{IMDb title|#=tt0213338}}") is None
    assert anilist_id("no templates here") is None


def test_a_dub_cast_is_detected_by_the_column_the_wiki_actually_uses():
    """Cast tables carry a "Dub Actor" column beside "Seiyu". Its presence
    says a dub cast is named; it does not say which language, and the
    census output must not claim otherwise."""
    from pipeline.probe_dub_fandom import names_a_dub_cast

    assert names_a_dub_cast("==Cast==\n!Character !!Seiyu !!Dub Actor\n")
    assert names_a_dub_cast("==Cast==\n! dub actor\n"), "case must not matter"
    assert not names_a_dub_cast("==Cast==\n!Character !!Seiyu\n")


def test_the_census_batches_its_reads():
    """634 pages one at a time is 634 requests against a volunteer wiki;
    MediaWiki takes fifty titles per query."""
    from pipeline.probe_dub_fandom import BATCH_SIZE

    assert BATCH_SIZE == 50


def test_the_census_asks_about_english_the_way_the_filter_does():
    """A known Portuguese cast is not an English dub answer.

    The first census counted any dub language as "answered", which put
    The Amazing 3 - AniList Portuguese only, ANN unmatched, and the title
    this whole investigation started from - in the answered column. The
    query must restate the English clause api/filters.py applies, or the
    measurement grades a different question than the app asks.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "pipeline/probe_dub_fandom.py").read_text()
    census = source[source.index("def run_census(") :]
    census = census[: census.index("\ndef ")]

    for clause in (
        "m.dub_languages @> ARRAY['en']::text[]",
        "m.measured_dub_languages @> ARRAY['en']::text[]",
        "m.english_va_count > 0",
        "m.english_dub_inferred",
    ):
        assert clause in census, f"the English test is missing {clause}"
    # And both figures are reported, so the difference between them stays
    # visible rather than being a silent choice of definition.
    assert "any_answer" in census and "english_answer" in census


def test_flag_templates_are_collected_for_inspection():
    """A page was observed carrying a dub COUNTRY, which is the language
    evidence the cast table withholds.

    Collected, not interpreted: a country is not a language, and the
    mapping has to be read off real pages before it decides anything a
    reader will see.
    """
    from pipeline.probe_dub_fandom import flags

    counted = flags("{{Flag|United States}} x {{Flag|Japan}} y {{Flag|United States}}")
    assert counted["United States"] == 2
    assert counted["Japan"] == 1
    # A flag with extra parameters is still that country.
    assert flags("{{Flag|Canada|size=20px}}")["Canada"] == 1
    assert flags("no flags here") == {}


MARINE_BOY = """==Dubbing History==
Re-packaged for the English-speaking market.
==Cast==
{|
! Image
! Character
! Seiyū {{Flag|Japan}}
! Dub Actor {{Flag|United States}}
|-
| Marine Boy || [[Yoshiko Ota]] || [[Corinne Orr]]
|}
==Transmission==
{{Flag|Australia}} {{Flag|United Kingdom}}
"""

AMAZING_3 = """==Dubbing History==
Dubbed by Copri International Films.
==Cast==
{|
!'''Character'''
!'''Dub Name'''
!'''Seiyū'''
!'''Dub Actor'''
|}
==Transmission==
{{Flag|United States}} {{Flag|Australia}} {{Flag|Japan}}
"""


def test_the_dub_language_is_read_from_the_column_header_only():
    """Per-dub evidence, not per-page.

    Marine Boy flags its Dub Actor column United States, which says what
    language that cast is in. The Amazing 3 flags United States and
    Australia in its Transmission section - those are broadcast countries,
    and reading them as a dub language would assert an English dub from
    the fact that a show once aired somewhere English-speaking.
    """
    from pipeline.probe_dub_fandom import dub_actor_countries, names_an_english_dub

    assert dub_actor_countries(MARINE_BOY) == ["United States"]
    assert names_an_english_dub(MARINE_BOY)

    assert dub_actor_countries(AMAZING_3) == []
    assert not names_an_english_dub(AMAZING_3), "a broadcast country is not a dub language"


def test_the_seiyu_column_flag_is_not_mistaken_for_the_dub():
    """Header cells can share one line, separated by !!.

    A line-level match would read the Seiyu column's Japan flag as the
    dub's country and report every such page as a Japanese dub.
    """
    from pipeline.probe_dub_fandom import dub_actor_countries

    line = "==Cast==\n! Character !! Seiyū {{Flag|Japan}} !! Dub Actor {{Flag|United States}}\n"
    assert dub_actor_countries(line) == ["United States"]


def test_canada_counts_as_english_but_is_tracked_separately():
    """The Vancouver studios dubbed a lot of anime into English, so Canada
    belongs in the set - and it is the one entry that can be wrong, since
    Canada also produces French dubs, so the census reports how many
    titles rest on it alone."""
    from pipeline.probe_dub_fandom import (
        AMBIGUOUS_ENGLISH_COUNTRIES,
        ENGLISH_DUB_COUNTRIES,
        names_an_english_dub,
    )

    assert "Canada" in ENGLISH_DUB_COUNTRIES
    assert AMBIGUOUS_ENGLISH_COUNTRIES == {"Canada"}
    assert names_an_english_dub("==Cast==\n! Dub Actor {{Flag|Canada}}\n")
    assert not names_an_english_dub("==Cast==\n! Dub Actor {{Flag|Brazil}}\n")
