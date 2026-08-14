"""Kitsu mapping harvest: JSON:API library entries to exclusion entries.

Payload shapes mirror Kitsu's live API (verified against the kitsu-server
source and Yamtrack's importer): entries reference media under
relationships[kind].data, media and their mappings arrive in `included`.
"""

from api.routers.kitsu import harvest_entries


def _entry(
    kind: str, media_id: str | None, status: str = "completed", rating: int | None = None
) -> dict:
    data = {"type": kind, "id": media_id} if media_id else None
    return {
        "id": f"entry-{media_id or 'hidden'}",
        "type": "libraryEntries",
        "attributes": {"status": status, "ratingTwenty": rating},
        "relationships": {kind: {"data": data}},
    }


def _media(kind: str, media_id: str, mapping_ids: list[str]) -> dict:
    return {
        "id": media_id,
        "type": kind,
        "relationships": {
            "mappings": {"data": [{"type": "mappings", "id": m} for m in mapping_ids]}
        },
    }


def _mapping(mapping_id: str, site: str, ext_id: str) -> dict:
    return {
        "id": mapping_id,
        "type": "mappings",
        "attributes": {"externalSite": site, "externalId": ext_id},
    }


def test_harvests_mal_and_anilist_mappings():
    entries = [_entry("anime", "10")]
    included = [
        _media("anime", "10", ["100", "101", "102"]),
        _mapping("100", "myanimelist/anime", "1"),
        _mapping("101", "anilist/anime", "5"),
        _mapping("102", "thetvdb/series", "80644"),
    ]
    harvested, skipped = harvest_entries("anime", entries, included)
    assert harvested == {("mal_anime", 1): (False, None), ("anilist", 5): (False, None)}
    assert skipped == 0


def test_manga_kind_maps_to_mal_manga():
    entries = [_entry("manga", "20")]
    included = [
        _media("manga", "20", ["200"]),
        _mapping("200", "myanimelist/manga", "7"),
    ]
    harvested, skipped = harvest_entries("manga", entries, included)
    assert harvested == {("mal_manga", 7): (False, None)}
    assert skipped == 0


def test_planned_status_sets_planned_flag():
    entries = [_entry("anime", "10", status="planned")]
    included = [
        _media("anime", "10", ["100"]),
        _mapping("100", "myanimelist/anime", "1"),
    ]
    harvested, skipped = harvest_entries("anime", entries, included)
    assert harvested == {("mal_anime", 1): (True, None)}
    assert skipped == 0


def test_started_entry_wins_over_planned_duplicate():
    # Two library entries resolving to the same external id: the started one
    # must keep the id excluded even with keep_planned on.
    entries = [
        _entry("anime", "50", status="planned"),
        _entry("anime", "51", status="current"),
    ]
    included = [
        _media("anime", "50", ["500"]),
        _media("anime", "51", ["501"]),
        _mapping("500", "myanimelist/anime", "42"),
        _mapping("501", "myanimelist/anime", "42"),
    ]
    harvested, skipped = harvest_entries("anime", entries, included)
    assert harvested == {("mal_anime", 42): (False, None)}
    assert skipped == 0


def test_hidden_media_counts_as_skipped():
    # Adult titles expose relationships[kind].data = null without auth.
    entries = [_entry("anime", None)]
    harvested, skipped = harvest_entries("anime", entries, [])
    assert harvested == {}
    assert skipped == 1


def test_non_numeric_external_id_counts_as_skipped():
    # Known Kitsu data bug: some MAL mappings hold "anime" instead of an id.
    entries = [_entry("anime", "30")]
    included = [
        _media("anime", "30", ["300"]),
        _mapping("300", "myanimelist/anime", "anime"),
    ]
    harvested, skipped = harvest_entries("anime", entries, included)
    assert harvested == {}
    assert skipped == 1


def test_wrong_kind_mapping_is_ignored():
    # An anime pass must not pick up manga-site mappings and vice versa:
    # MAL anime and manga ids are separate id spaces.
    entries = [_entry("anime", "40")]
    included = [
        _media("anime", "40", ["400"]),
        _mapping("400", "myanimelist/manga", "9"),
    ]
    harvested, skipped = harvest_entries("anime", entries, included)
    assert harvested == {}
    assert skipped == 1


def test_rating_twenty_normalizes_to_score():
    entries = [_entry("anime", "60", rating=18)]
    included = [
        _media("anime", "60", ["600"]),
        _mapping("600", "myanimelist/anime", "11"),
    ]
    harvested, _ = harvest_entries("anime", entries, included)
    assert harvested == {("mal_anime", 11): (False, 90)}


def test_duplicate_entries_keep_higher_score():
    entries = [
        _entry("anime", "70", rating=10),
        _entry("anime", "71", rating=16),
    ]
    included = [
        _media("anime", "70", ["700"]),
        _media("anime", "71", ["701"]),
        _mapping("700", "myanimelist/anime", "12"),
        _mapping("701", "myanimelist/anime", "12"),
    ]
    harvested, _ = harvest_entries("anime", entries, included)
    assert harvested == {("mal_anime", 12): (False, 80)}
