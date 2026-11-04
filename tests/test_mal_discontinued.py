"""MAL list sync is dead at the source, and the error has to say so.

A refresh returned {"detail": "Jikan unreachable"}. That was true and it
cost an investigation: it pointed at the transport, so the next steps were
container DNS, egress rules and the proxy - none of which could have helped,
because Jikan's own OpenAPI spec marks exactly two endpoints deprecated out
of its whole v4 surface and they are the two this module calls, with the
description "User Anime lists have been discontinued since May 1st, 2022".

These pin the honesty of the failure rather than the failure itself. A
message naming only the nearest cause is worse than a vague one, because it
is confidently wrong about where to look.
"""

import re
from pathlib import Path

import pytest

from api.routers.mal import DISCONTINUED, JIKAN_BASE, LIST_TYPES

ROOT = Path(__file__).resolve().parent.parent
MAL = ROOT / "api" / "routers" / "mal.py"


def test_the_message_names_the_real_cause_not_just_the_transport():
    text = DISCONTINUED.lower()
    assert "discontinued" in text
    assert "2022" in text


def test_the_message_points_at_something_that_works():
    """A dead end is only actionable if it names the live path.

    AniList and Kitsu read lists from reachable, non-deprecated APIs and
    feed the same exclusion machinery, so the user is one checkbox from a
    working equivalent - which is worth saying in the error itself.
    """
    text = DISCONTINUED.lower()
    assert "anilist" in text
    assert "kitsu" in text


@pytest.mark.parametrize("status", [404, 502])
def test_both_failure_paths_carry_it(status):
    """404 and the generic Jikan error must not revert to a bare message."""
    source = MAL.read_text()
    raises = re.findall(r"raise HTTPException\((.*?)\) from exc", source, re.S)
    assert len(raises) >= 3, "expected the 404, status and transport paths"
    transport = [r for r in raises if "DISCONTINUED" in r]
    assert len(transport) >= 2, raises


def test_the_dead_endpoints_are_recorded_where_they_are_called():
    """So the next reader does not re-derive it from a transport error."""
    source = MAL.read_text()
    assert "animelist" in source and "mangalist" in source
    assert "deprecated" in source.lower()
    assert "May 1st, 2022" in source


def test_the_working_sources_are_not_on_the_same_host():
    """Pins why AniList and Kitsu are a real recommendation, not a hope."""
    for router, host in (("anilist.py", "graphql.anilist.co"), ("kitsu.py", "kitsu.app")):
        text = (ROOT / "api" / "routers" / router).read_text()
        assert host in text, router
        assert JIKAN_BASE not in text, router


def test_the_exclusion_itself_is_untouched_by_this():
    """Only the fetch is dead. The id_mal join still has to work.

    MAL anime and manga are separate id spaces, so the filter joins on
    list_type as well as id_mal - that logic is independent of how entries
    got stored, and a dead fetch must not be read as a dead feature.
    """
    filters = (ROOT / "api" / "filters.py").read_text()
    assert "mal_list_entries" in filters
    assert "lower(m.media_type)" in filters
    assert set(LIST_TYPES) == {"anime", "manga"}
