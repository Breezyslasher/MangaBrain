"""The dub explainer must name the right stage, especially the pending one.

Two real false negatives prompted this - The Amazing 3 (1965) and Discode,
both with English dubs and neither marked - and the point of the tool is to
say WHICH stage lost a title rather than that one did. The classification is
therefore the thing worth pinning: a tool that confidently blames AniList
for a row no pass has visited yet would send the next investigation after a
source limitation that is not there.
"""

import pytest

from pipeline.ann import AnnEntry, build_index
from pipeline.explain_dub import ask_anilist, ask_ann, show_database


def make_row(**over) -> dict:
    row = {
        "id": 1,
        "title_romaji": "Wonder 3",
        "title_english": "The Amazing 3",
        "title_native": "W3",
        "synonyms": [],
        "start_year": 1965,
        "format": "TV",
        "country_of_origin": "JP",
        "english_va_count": 0,
        "cast_character_count": 0,
        "english_dub_inferred": False,
        "dub_languages": [],
        "dub_languages_source": None,
        "ann_id": None,
        "measured_dub_languages": [],
        "measured_dub_checked_at": None,
        "dub_checked_at": None,
    }
    row.update(over)
    return row


class FakeAniList:
    def __init__(self, media: dict | None):
        self.media = media

    def request(self, *args, **kwargs):
        page = {"media": [self.media] if self.media else []}
        return {"data": {"Page": page}}


def media_with(actors: list[tuple[str, str]], characters: int = 1) -> dict:
    """One AniList media payload. actors is (id, languageV2) pairs."""
    edges = [
        {
            "node": {"id": i},
            "voiceActors": [{"id": aid, "languageV2": lang} for aid, lang in actors],
        }
        for i in range(characters)
    ]
    return {"id": 1, "countryOfOrigin": "JP", "characters": {"edges": edges}}


def test_unvisited_row_is_pending_not_a_gap(capsys):
    """An unrun pass is not evidence of anything.

    The whole dub design treats missing data as not-evidence (the filter is
    positive-only, english_va_count = 0 is never served as sub-only), and the
    explainer has to hold the same line or it will send someone chasing a
    source limitation that does not exist.
    """
    notes = show_database(make_row())
    capsys.readouterr()
    assert any(n.startswith("PENDING") for n in notes), notes


def test_visited_row_is_not_reported_as_pending(capsys):
    notes = show_database(
        make_row(dub_checked_at="2026-01-01", measured_dub_checked_at="2026-01-01")
    )
    capsys.readouterr()
    assert not any(n.startswith("PENDING") for n in notes), notes


def test_english_only_pass_is_reported_as_partial(capsys):
    notes = show_database(make_row(dub_checked_at="2026-01-01"))
    capsys.readouterr()
    assert any(n.startswith("PARTIAL") for n in notes), notes


def test_no_cast_at_all_is_case_two(capsys):
    notes = ask_anilist(FakeAniList(media_with([], characters=0)), 1)
    capsys.readouterr()
    assert any("no cast whatsoever" in n for n in notes), notes


def test_cast_without_english_is_case_three(capsys):
    """The Berserk (2017) shape: 21 characters, no English, real dub."""
    notes = ask_anilist(FakeAniList(media_with([("9", "Japanese")], characters=21)), 1)
    capsys.readouterr()
    assert any("no English in it (case 3)" in n for n in notes), notes


def test_english_present_upstream_points_back_at_the_walk(capsys):
    """If AniList credits English now, the pipeline is behind, not blind."""
    notes = ask_anilist(
        FakeAniList(
            media_with([("9", "English")]),
        ),
        1,
    )
    capsys.readouterr()
    assert any("re-run the language walk" in n for n in notes), notes


def test_missing_id_upstream_is_reported(capsys):
    notes = ask_anilist(FakeAniList(None), 1)
    capsys.readouterr()
    assert any("no longer serves this id" in n for n in notes), notes


def test_rejection_names_the_guard_and_the_entry(capsys, monkeypatch):
    """A rejected match must be shown, and shown as rejected.

    match() withholds the entry on a rejection so it can never be stored by
    accident - that invariant is pinned elsewhere and exists because a
    2-character Suntory advert was once written as a dub for 'your name.'.
    The explainer needs the entry anyway to say what was contradicted, so it
    goes through rejected_against; this checks it reports both the guard and
    the entry, and does not quietly lose the rejection.
    """
    # Same name, format ANN calls a commercial: the format guard fires.
    index = build_index([AnnEntry(ann_id="99", name="The Amazing 3", kind="TV", year=1800)])
    monkeypatch.setattr(
        "pipeline.explain_dub.fetch_casts", lambda client, ids: {"99": (3, {"EN", "JA"})}
    )
    notes = ask_ann(None, index, make_row())
    out = capsys.readouterr().out
    assert "verdict: rejected_year" in out
    assert any("year guard rejected it" in n for n in notes), notes
    # And it must say the guard cost something real, since ANN holds a dub.
    assert any("cost a real match" in n for n in notes), notes


def test_exact_match_with_english_blames_the_unrun_ann_pass(capsys, monkeypatch):
    index = build_index([AnnEntry(ann_id="99", name="The Amazing 3", kind="TV", year=1965)])
    monkeypatch.setattr(
        "pipeline.explain_dub.fetch_casts", lambda client, ids: {"99": (3, {"EN", "JA"})}
    )
    notes = ask_ann(None, index, make_row())
    capsys.readouterr()
    assert any("ANN KNOWS about an English dub" in n for n in notes), notes


def test_native_language_is_not_called_a_dub(capsys, monkeypatch):
    """A Japanese cast on a Japanese show is the original, not a dub."""
    index = build_index([AnnEntry(ann_id="99", name="The Amazing 3", kind="TV", year=1965)])
    monkeypatch.setattr("pipeline.explain_dub.fetch_casts", lambda client, ids: {"99": (0, {"JA"})})
    notes = ask_ann(None, index, make_row())
    out = capsys.readouterr().out
    assert "none recorded" in out
    assert any("records no English cast either" in n for n in notes), notes


def test_unmatched_is_case_five(capsys):
    notes = ask_ann(None, build_index([]), make_row())
    capsys.readouterr()
    assert any("case 5" in n for n in notes), notes


def test_parent_only_match_is_not_claimed_for_the_entry(capsys, monkeypatch):
    """A parent's dub languages are not this entry's.

    The ANN pass deliberately never writes a base match, so the explainer
    must not report one as an answer either - it points at propagation.
    """
    index = build_index([AnnEntry(ann_id="99", name="Wonder", kind="TV", year=1965)])
    monkeypatch.setattr("pipeline.explain_dub.fetch_casts", lambda client, ids: {"99": (3, {"EN"})})
    row = make_row(title_english=None, title_romaji="Wonder 3", title_native=None)
    notes = ask_ann(None, index, row)
    out = capsys.readouterr().out
    assert "verdict: base" in out
    assert any("only the parent series" in n for n in notes), notes
    assert any("propagation territory" in n for n in notes), notes


@pytest.mark.parametrize("country,langs,expect_dub", [("JP", {"JA"}, False), ("CN", {"JA"}, True)])
def test_country_decides_which_language_is_native(capsys, monkeypatch, country, langs, expect_dub):
    """A Japanese cast on a Chinese donghua genuinely is a dub."""
    index = build_index([AnnEntry(ann_id="99", name="The Amazing 3", kind="TV", year=1965)])
    monkeypatch.setattr("pipeline.explain_dub.fetch_casts", lambda client, ids: {"99": (0, langs)})
    ask_ann(None, index, make_row(country_of_origin=country))
    out = capsys.readouterr().out
    assert ("ANN dub languages: ['ja']" in out) is expect_dub, out
