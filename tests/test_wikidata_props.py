"""Choosing Wikidata's anime identifier property.

The first run of the Wikidata probe reported that only 3 items carry voice
actor statements, which read as "Wikidata has essentially no dub data". It
was an artefact of property selection: the probe matched labels containing
both "myanimelist" and "anime", and "anime" occurs INSIDE "myanimelist" -
myanimelist[2:7] is literally "anime" - so "MyAnimeList people ID" matched
first and the probe joined voice actors against PEOPLE ids.

A wrong property id in SPARQL returns a confident empty result rather than
an error, so there was nothing to notice except a number that happened to
look like a finding.
"""

import pytest


def test_the_substring_trap_is_real():
    """The bug itself, pinned so the shortcut is not reintroduced."""
    assert "anime" in "MyAnimeList people ID".lower()
    assert "anime" in "myanimelist"
    assert "myanimelist"[2:7] == "anime"


def test_exact_labels_pick_the_anime_property():
    from pipeline.probe_wikidata import choose_id_property

    discovered = {
        "MyAnimeList people ID": "P4084",
        "MyAnimeList character ID": "P4085",
        "MyAnimeList anime ID": "P4086",
        "MyAnimeList manga ID": "P4087",
        "AniList anime ID": "P8729",
    }
    name, pid = choose_id_property(discovered)
    assert (name, pid) == ("MyAnimeList anime ID", "P4086")


def test_preference_falls_through_in_order():
    from pipeline.probe_wikidata import choose_id_property

    # No MyAnimeList anime ID: AniList is next, not a people or manga id.
    discovered = {
        "MyAnimeList people ID": "P4084",
        "MyAnimeList manga ID": "P4087",
        "AniList anime ID": "P8729",
        "Kitsu anime ID": "P11495",
    }
    assert choose_id_property(discovered) == ("AniList anime ID", "P8729")


def test_a_people_or_manga_property_is_never_chosen():
    from pipeline.probe_wikidata import choose_id_property

    discovered = {
        "MyAnimeList people ID": "P4084",
        "MyAnimeList manga ID": "P4087",
        "AniList staff ID": "P11227",
        "AniList manga ID": "P8731",
    }
    with pytest.raises(SystemExit):
        choose_id_property(discovered)


def test_nothing_discovered_fails_loudly():
    """Better to stop than to join on whatever came back first."""
    from pipeline.probe_wikidata import choose_id_property

    with pytest.raises(SystemExit):
        choose_id_property({})
