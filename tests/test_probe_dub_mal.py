"""MAL probe: the bands, the native-language rule, and the error taxonomy.

The probe's whole purpose is to say whether MAL knows about dubs in the
OBSCURE bands, so a banding bug would answer a different question than the
one asked. And the error taxonomy is the part that has gone wrong before:
four measurements in this investigation reported a probe's own failure as a
finding, so a 404, a failed request and a real "no English cast" are kept
apart and each is pinned here.
"""

import re
from pathlib import Path

import httpx
import pytest

from pipeline.probe_dub_mal import GAP_SQL, band_of, dub_codes, mal_languages

FILTERS = Path(__file__).resolve().parent.parent / "api" / "filters.py"


@pytest.mark.parametrize(
    "popularity,band",
    [
        (500000, "top (100k+)"),
        (100000, "top (100k+)"),
        (99999, "20k-100k"),
        (20000, "20k-100k"),
        (19999, "5k-20k"),
        (1000, "1k-5k"),
        (999, "100-1k"),
        (100, "100-1k"),
        (99, "under 100"),
        (0, "under 100"),
        (None, "unknown"),
    ],
)
def test_bands_are_contiguous_and_cover_every_value(popularity, band):
    """No value may fall through, or the sample silently skips titles."""
    assert band_of(popularity) == band


def test_native_language_is_not_counted_as_a_dub():
    codes, unknown = dub_codes({"Japanese", "English"}, "JP")
    assert codes == {"en"}
    assert unknown == set()


def test_japanese_on_a_donghua_is_a_dub():
    """Which language is native depends on the title, not the medium."""
    codes, _unknown = dub_codes({"Japanese"}, "CN")
    assert codes == {"ja"}


def test_unmapped_language_names_are_reported_not_dropped():
    """MAL writes 'Portuguese (BR)', which no code map has.

    Dropping it would make a vocabulary gap look like an absence of dubs,
    which is the single mistake this investigation keeps having to undo.
    """
    codes, unknown = dub_codes({"Portuguese (BR)"}, "JP")
    assert unknown == {"Portuguese (BR)"}
    assert codes, "an unmapped name still has to appear as some code"


def test_no_languages_at_all_is_empty_not_an_error():
    assert dub_codes(set(), "JP") == (set(), set())


class StubClient:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc

    def request(self, *args, **kwargs):
        if self.exc:
            raise self.exc
        return self.result


def status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.jikan.moe/v4/anime/1/characters")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_missing_mal_entry_is_absent_not_no_english():
    """A 404 is a real answer about coverage, and not about dubs."""
    langs, status = mal_languages(StubClient(exc=status_error(404)), 1)
    assert (langs, status) == (set(), "absent")


def test_other_http_errors_are_failures_not_no_english():
    """A swallowed 400 once printed as '0/200 (0.0%)' in this investigation."""
    for code in (400, 403, 500):
        langs, status = mal_languages(StubClient(exc=status_error(code)), 1)
        assert (langs, status) == (set(), "failed"), code


def test_transport_errors_are_failures():
    langs, status = mal_languages(StubClient(exc=httpx.ConnectError("refused")), 1)
    assert (langs, status) == (set(), "failed")


def test_languages_are_read_from_every_character():
    payload = {
        "data": [
            {"voice_actors": [{"language": "Japanese"}, {"language": "English"}]},
            {"voice_actors": [{"language": "German"}]},
            {"voice_actors": []},
            {},
        ]
    }
    langs, status = mal_languages(StubClient(result=payload), 1)
    assert status == "ok"
    assert langs == {"Japanese", "English", "German"}


def test_an_answered_title_with_no_cast_is_ok_and_empty():
    """Distinct from absent and from failed: MAL answered, there is no cast."""
    langs, status = mal_languages(StubClient(result={"data": []}), 1)
    assert (langs, status) == (set(), "ok")


def test_gap_set_matches_the_filter_the_ui_serves():
    """Drift guard: the probe must measure the gap users actually have.

    Same check as the audit's, for the same reason - a probe measuring a
    different gap than the dub filter serves would report a recovery that
    does not reach anyone.
    """
    clause = GAP_SQL[GAP_SQL.index("AND NOT (") :]
    probe_columns = set(re.findall(r"m\.(\w+)", clause))
    source = FILTERS.read_text()
    start = source.index('if code == "en":')
    filter_columns = set(re.findall(r"m\.(\w+)", source[start : source.index("else:", start)]))
    assert probe_columns == filter_columns, (probe_columns, filter_columns)
