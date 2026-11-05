"""MAL list sync, after moving off the endpoints MAL discontinued.

A refresh used to return {"detail": "Jikan unreachable"}. That was true and
it cost an investigation: it pointed at the transport, so the next steps
were container DNS, egress rules and the proxy - none of which could have
helped, because Jikan's own OpenAPI spec marks exactly two endpoints
deprecated out of its whole v4 surface and they were the two this module
called, described as "User Anime lists have been discontinued since May
1st, 2022".

The route that works is the official API, found because the user asked how
Yamtrack reads the same lists from the same host. These tests pin the move
and the two details of it that would fail silently: the credential and the
status vocabulary.
"""

import re
from pathlib import Path

from api.routers.mal import LIST_TYPES, NO_CLIENT_ID, UNREACHABLE

ROOT = Path(__file__).resolve().parent.parent
MAL = ROOT / "api" / "routers" / "mal.py"


def test_the_credential_error_says_where_to_get_one():
    """A missing client id is a setup step, not a fault, so the message
    has to name the page and the setting rather than report a refusal."""
    text = NO_CLIENT_ID.lower()
    assert "myanimelist.net/apiconfig" in text
    assert "mal_client_id" in text


def test_the_failure_message_still_points_at_something_that_works():
    """A dead end is only actionable if it names the live path: AniList
    and Kitsu need no credential and feed the same exclusion."""
    for text in (NO_CLIENT_ID.lower(), UNREACHABLE.lower()):
        assert "anilist" in text and "kitsu" in text


def test_every_failure_path_explains_itself():
    """404, 401/403 and the transport error are different problems with
    different fixes, and each has to say which it is."""
    source = MAL.read_text()
    raises = re.findall(r"raise HTTPException\((.*?)\) from exc", source, re.S)
    assert len(raises) >= 4, "expected not-found, rejected-credential, error and transport"
    assert any("not found" in r for r in raises)
    assert any("NO_CLIENT_ID" in r for r in raises)
    assert any("UNREACHABLE" in r for r in raises)


def test_why_jikan_is_not_used_is_recorded_where_someone_would_re_add_it():
    """So the next reader does not "fix" this by pointing it back at the
    simpler API that needs no credential."""
    source = MAL.read_text()
    assert "animelist" in source and "mangalist" in source
    assert "discontinued" in source.lower()
    assert "May 1st, 2022" in source


def test_both_list_types_are_still_fetched():
    assert LIST_TYPES == ("anime", "manga")


def test_the_list_comes_from_the_official_api_now():
    """Jikan's user-list endpoints are discontinued; the official API is
    the only working route, and it is how Yamtrack reads the same lists
    from the same host."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "api/routers/mal.py").read_text()
    assert "https://api.myanimelist.net/v2" in source
    assert "X-MAL-CLIENT-ID" in source
    assert "api.jikan.moe" not in source, "the list must not go back through Jikan"
    # Paging is an absolute next URL, not a page number: ?page=N was not in
    # Jikan's spec for these endpoints and paged nothing.
    assert '"next"' in source or "paging" in source


def test_a_missing_client_id_is_refused_before_the_request():
    """So the error names the missing credential rather than whatever the
    API says to an unauthenticated caller."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "api/routers/mal.py").read_text()
    refresh = source[source.index("def refresh_mal_lists(") :]
    refresh = refresh[: refresh.index("\n@router")]
    assert "if not settings.mal_client_id:" in refresh
    assert refresh.index("if not settings.mal_client_id:") < refresh.index("RateLimitedClient(")


def test_keep_planned_understands_both_status_vocabularies():
    """The official API sends "plan_to_watch"; Jikan sent "6".

    A list cached before the switch must still honour the toggle, or
    keep_planned silently stops working for rows nobody re-synced.
    """
    from api.filters import build_filters

    sql, _ = build_filters(mal_user="someone", keep_planned=True)
    assert "l.status NOT IN ('6', 'plan_to_watch', 'plan_to_read')" in sql

    without, _ = build_filters(mal_user="someone", keep_planned=False)
    assert "plan_to_watch" not in without, "every status excludes when the toggle is off"


def test_a_list_with_no_statuses_is_refused_rather_than_stored():
    """MAL answers 200 to an unknown field name instead of rejecting it.

    Measured against the live API: fields=characters, voice_actors and cast
    all return the default payload with no such key and no error. So a typo
    in LIST_PARAMS would not fail - it would store a complete list in which
    every entry has an empty status and no score, which looks like a
    successful sync while silently disabling keep_planned and flattening
    the For-you rating weights.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "api/routers/mal.py").read_text()
    fetch = source[source.index("def _fetch_list(") :]
    fetch = fetch[: fetch.index("\n@router")]
    assert "with_status" in fetch
    assert "if entries and not with_status:" in fetch
    assert "cached list is unchanged" in fetch


def test_the_absence_of_cast_data_is_recorded_as_measured():
    """So nobody re-tries fields=characters on the assumption it was never
    checked - it was, and the API answered 200 with nothing."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "api/routers/mal.py").read_text()
    assert "fields=characters" in source
    assert "voice_actors" in source


def test_the_ci_probe_exercises_the_router_not_a_copy_of_it():
    """A reimplementation would drift, and the first thing it would stop
    catching is the reason the guard exists: MAL answers 200 to an unknown
    field, so a typo returns a full list with no statuses and no error."""
    from pathlib import Path

    probe = (Path(__file__).resolve().parent.parent / "pipeline/probe_mal_list.py").read_text()
    assert "from api.routers.mal import LIST_TYPES, _fetch_list" in probe
    assert "api.myanimelist.net" not in probe, "the URL belongs to the router"


def test_the_credential_reaches_ci_as_a_secret_not_a_commit():
    """A MAL client id is not an OAuth secret, but its rate limit is the
    owner's, and this repository has no reason to publish one."""
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parent.parent / ".github/workflows/mal-list-probe.yml"
    ).read_text()
    assert "secrets.MAL_CLIENT_ID" in workflow
    # A literal 32-hex client id must never appear in the workflow.
    import re

    assert not re.search(r"\b[0-9a-f]{32}\b", workflow)
