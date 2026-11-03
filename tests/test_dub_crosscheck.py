"""Which names the cross-check offers ANN, and how a match is resolved.

The matching rules themselves live in pipeline/ann.py and are tested in
test_ann_index.py. What matters here is that every name AniList knows a title
by gets offered, English first, because ANN files entries under their release
title: "Shuumatsu no Harem" is "World's End Harem" there. An earlier run asked
with romaji only and resolved 2 of 17.
"""

from pipeline.ann import AnnEntry, build_index
from pipeline.probe_dub_crosscheck import candidate_titles, resolve_media


def test_english_title_is_offered_before_romaji():
    media = {
        "title": {
            "english": "World's End Harem",
            "romaji": "Shuumatsu no Harem",
            "native": "終末のハーレム",
        },
        "synonyms": ["Last Man on Earth"],
    }
    names = candidate_titles(media)
    assert names[0] == "World's End Harem"
    assert "Shuumatsu no Harem" in names
    assert "終末のハーレム" in names


def test_romaji_is_used_when_there_is_no_english_name():
    # capeta and Shadowverse both resolved on romaji alone.
    assert candidate_titles({"title": {"english": None, "romaji": "capeta"}}) == ["capeta"]


def test_candidates_dedupe_on_the_normalised_form():
    media = {
        "title": {"english": "Kuma Miko", "romaji": "kuma-miko!", "native": None},
        "synonyms": ["KUMA MIKO"],
    }
    # All three normalise identically, so one key is offered, not three.
    assert len(candidate_titles(media)) == 1


def test_missing_titles_are_skipped_rather_than_offered_blank():
    assert candidate_titles({"title": {}, "synonyms": None}) == []


def test_resolve_uses_the_entrys_year_and_format_to_disambiguate():
    """A shared base name is only safe to accept once year and format agree."""
    index = build_index(
        [
            AnnEntry("1", "Beastars", "TV", 2019),
            AnnEntry("2", "Beastars", "ONA", 2026),
        ]
    )
    media = {
        "title": {"english": "BEASTARS Final Season Part 2", "romaji": None, "native": None},
        "synonyms": [],
        "startDate": {"year": 2026},
        "format": "ONA",
    }
    verdict, entry = resolve_media(index, media)
    assert verdict == "base"
    assert entry is not None and entry.ann_id == "2"


def test_resolve_reports_ambiguity_rather_than_picking_one():
    index = build_index(
        [
            AnnEntry("1", "Beastars", "TV", 2019),
            AnnEntry("2", "Beastars", "ONA", 2026),
        ]
    )
    media = {
        "title": {"english": "Beastars", "romaji": None, "native": None},
        "synonyms": [],
        "startDate": {"year": None},
        "format": None,
    }
    verdict, entry = resolve_media(index, media)
    assert verdict.startswith("ambiguous")
    assert entry is None


def test_resolve_tolerates_a_missing_start_date():
    index = build_index([AnnEntry("3", "Capeta", "TV", 2005)])
    media = {"title": {"english": "Capeta"}, "synonyms": [], "format": "TV"}
    assert resolve_media(index, media)[0] == "exact"


def test_an_english_release_title_hiding_in_synonyms_resolves():
    """AniList often has no `english` field but lists it as a synonym.

    Matching is a dictionary lookup against the bulk index now, not a request
    per candidate, so every name is tried. Capping the candidates was a
    leftover from the search era and cost real recall: 45% of silent titles
    went unresolved, and ANN files this one under the English name.
    """
    index = build_index([AnnEntry("1", "My Wife is the Student Council President", "TV", 2015)])
    media = {
        "title": {
            "english": None,
            "romaji": "Okusama ga Seitokaichou!",
            "native": "おくさまが生徒会長！",
        },
        "synonyms": ["OkuSei", "Okusama+!", "My Wife is the Student Council President"],
        "startDate": {"year": 2015},
        "format": "TV",
    }
    verdict, entry = resolve_media(index, media)
    assert verdict == "exact"
    assert entry is not None and entry.ann_id == "1"


def test_widening_the_candidates_does_not_reopen_the_bad_join():
    """A synonym match is how the Suntory advert hit 'your name.'.

    Trying more names is only safe because corroboration rejects a match whose
    year or format contradicts it, so that case must stay rejected.
    """
    index = build_index([AnnEntry("18171", "your name.", "movie", 2016)])
    media = {
        "title": {"english": None, "romaji": "Suntory Minami Alps no Tennen Mizu"},
        "synonyms": ["your name.", "Suntory CM"],
        "startDate": {"year": 2016},
        "format": "SPECIAL",
    }
    assert resolve_media(index, media) == ("rejected_format", None)
