"""The Android client's models must track the API they were generated from.

A second client duplicates knowledge of the API, and that is the real cost
of having one: add a field to MediaOut and the web client gets it for free,
while the Kotlin data class silently does not. This fails the build instead,
and names the field.

The schema comes from the FastAPI app itself rather than a checked-in copy,
so there is nothing to regenerate and nothing to go stale.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "android/app/src/main/java/com/mangabrain/app/data/api/Models.kt"
API_FILE = ROOT / "android/app/src/main/java/com/mangabrain/app/data/api/MangaBrainApi.kt"

pytestmark = pytest.mark.skipif(not MODELS.exists(), reason="android client not present")

CHECKED = [
    "MediaOut",
    "SearchResponse",
    "ScoreComponents",
    "RecommendationItem",
    "RelatedItem",
    "RecommendResponse",
    "AppSettingsOut",
    "AppSettingsIn",
    "DatasetOut",
    "DatasetInstallOut",
    "DatasetsOut",
]


def schema() -> dict:
    from api.main import app

    return app.openapi()["components"]["schemas"]


def kotlin_classes() -> dict[str, dict[str, tuple[str, bool]]]:
    """{class: {field: (type, has_default)}}, parsed line-wise.

    Deliberately not a single regex over the parenthesised body: an
    `emptyList()` default contains a closing bracket, so `[^)]*` stops at the
    first one and under-reports every class with a list field. The first
    version of this check did exactly that and reported 20 fields missing
    from MediaOut that were present all along.
    """
    out: dict[str, dict[str, tuple[str, bool]]] = {}
    name: str | None = None
    fields: dict[str, tuple[str, bool]] = {}
    for line in MODELS.read_text().split("\n"):
        header = re.match(r"data class (\w+)\(", line)
        if header:
            name, fields = header.group(1), {}
            continue
        if name is None:
            continue
        if re.match(r"^\)", line):
            out[name] = fields
            name = None
            continue
        field = re.match(r"\s*val (\w+):\s*(.+?)(?:\s*=\s*(.+?))?\s*,?\s*$", line)
        if field:
            fields[field.group(1)] = (field.group(2), field.group(3) is not None)
    return out


@pytest.mark.parametrize("name", CHECKED)
def test_every_api_field_exists_on_the_kotlin_model(name):
    api_fields = set(schema()[name].get("properties", {}))
    kotlin = kotlin_classes().get(name)
    assert kotlin is not None, f"{name} is not declared in Models.kt"
    missing = sorted(api_fields - set(kotlin))
    assert missing == [], f"{name} is missing fields the API returns: {missing}"


@pytest.mark.parametrize("name", CHECKED)
def test_no_kotlin_field_the_api_does_not_send(name):
    """An invented field is always null, which reads as missing data."""
    api_fields = set(schema()[name].get("properties", {}))
    extra = sorted(set(kotlin_classes()[name]) - api_fields)
    assert extra == [], f"{name} declares fields the API does not send: {extra}"


@pytest.mark.parametrize("name", CHECKED)
def test_optional_fields_have_kotlin_defaults(name):
    """Moshi throws on an absent field with no default, so this is a crash.

    MediaOut has 26 optional fields out of 28; any response that omits one
    would fail to parse and take the whole screen down with it.
    """
    model = schema()[name]
    optional = set(model.get("properties", {})) - set(model.get("required", []))
    kotlin = kotlin_classes()[name]
    undefaulted = sorted(f for f in optional & set(kotlin) if not kotlin[f][1])
    assert undefaulted == [], f"{name} optional fields need defaults: {undefaulted}"


ENDPOINTS = {
    "search": "/search",
    "recommend": "/recommend/{media_id}",
    "recommendMix": "/recommend",
    "forYou": "/foryou",
    "random": "/random",
}

FILTER_STATE = ROOT / "android/app/src/main/java/com/mangabrain/app/data/FilterState.kt"


def server_params(path: str) -> set[str]:
    return {p["name"] for p in schema_parameters(path)}


def schema_parameters(path: str) -> list[dict]:
    from api.main import app

    return app.openapi()["paths"][path]["get"].get("parameters", [])


@pytest.mark.parametrize("method,path", sorted(ENDPOINTS.items()))
def test_query_parameters_exist_on_the_server(method, path):
    """A @Query name the server does not read is silently ignored.

    FastAPI drops unknown query parameters rather than erroring, so a typo
    here means the app quietly asks for something else - a dub filter that
    does not filter. /search spells it exclude_lists while /recommend spells
    it exclude_list, which is exactly the kind of difference this catches.
    """
    source = API_FILE.read_text()
    block = source[source.index(f"fun {method}(") :]
    block = block[: block.index("):")]
    used = set(re.findall(r'@Query\("([^"]+)"\)', block))
    unknown = sorted(used - server_params(path))
    assert unknown == [], f"{path} has no such query parameter(s): {unknown}"


def test_filter_state_only_builds_parameters_the_server_reads():
    """The scalar half of every request, which no @Query annotation covers.

    FilterState assembles twenty-odd parameters into a QueryMap, so they are
    plain strings in Kotlin and nothing in the type system checks them.
    Checked against the union of the endpoints that receive that map.
    """
    assert FILTER_STATE.exists(), "FilterState.kt is missing"
    known: set[str] = set()
    for path in ENDPOINTS.values():
        known |= server_params(path)
    source = FILTER_STATE.read_text()
    # From the first parameter builder onward. Everything above it is state
    # and bookkeeping that happens to use the same Kotlin `to` syntax - the
    # per-medium UNSUPPORTED map reads "book" to setOf(...) - and scanning
    # it reported a medium name as a query parameter no endpoint reads.
    source = source[source.index("fun filterParams") :]
    # params["x"] = ... and "x" to ... are the two shapes that file uses.
    used = set(re.findall(r'params\["([a-z_]+)"\]', source))
    used |= set(re.findall(r'"([a-z_]+)" to ', source))
    unknown = sorted(used - known)
    assert unknown == [], f"FilterState sends parameters no endpoint reads: {unknown}"


def test_the_two_spellings_of_the_exclusion_parameter_are_both_honoured():
    """A real bug this pair of names already caused.

    The web client sent exclude_list to /search, which declares
    exclude_lists, so Kitsu and Yamtrack exclusions were silently dropped
    from every filtered browse. Both clients now spell it per endpoint.
    """
    assert "exclude_lists" in server_params("/search")
    assert "exclude_list" in server_params("/recommend/{media_id}")
    api = API_FILE.read_text()
    search_block = api[api.index("fun search(") : api.index("fun recommend(")]
    assert '@Query("exclude_lists")' in search_block
    recommend_block = api[api.index("fun recommend(") : api.index("fun recommendMix(")]
    assert '@Query("exclude_list")' in recommend_block


def test_tag_autocomplete_matches_the_web_client():
    """Same ranking and the same cap in both clients.

    Android's stock ArrayAdapter filter matches only the start of a string
    or of a word inside it, so "rav" would miss "Time Travel" entirely. The
    client ships a filter that mirrors the web one - prefix matches first,
    then substring - and this pins the cap so the two cannot drift into
    showing different numbers of suggestions for the same typing.
    """
    adapter = (
        ROOT / "android/app/src/main/java/com/mangabrain/app/ui/filters/TagSuggestionAdapter.kt"
    )
    assert adapter.exists(), "TagSuggestionAdapter.kt is missing"
    kotlin = adapter.read_text()
    javascript = (ROOT / "web/app.js").read_text()

    kotlin_cap = re.search(r"MAX_SUGGESTIONS\s*=\s*(\d+)", kotlin)
    web_cap = re.search(r"MAX_TAG_SUGGESTIONS\s*=\s*(\d+)", javascript)
    assert kotlin_cap and web_cap, "both clients must declare a suggestion cap"
    assert kotlin_cap.group(1) == web_cap.group(1), (
        f"suggestion caps differ: Kotlin {kotlin_cap.group(1)}, web {web_cap.group(1)}"
    )

    # Both rank prefix matches above substring matches.
    assert "startsWith(query)" in kotlin and "contains(query)" in kotlin
    assert "startsWith(q)" in javascript and "includes(q)" in javascript


def test_responses_are_compressed():
    """Large JSON over a phone connection has to be gzipped.

    /recommend returns up to 200 MediaOut objects and each carries its full
    synopsis, so an uncompressed response is a few hundred kilobytes for one
    tap. FastAPI adds no compression on its own, and the omission is silent:
    everything works, just slowly, and only over a slow link.
    """
    from api.main import app

    assert "GZipMiddleware" in [m.cls.__name__ for m in app.user_middleware]


def test_the_android_client_does_not_ask_for_more_than_it_shows():
    """The grid has no paging, so a large limit is pure download cost."""
    source = FILTER_STATE.read_text()
    limit = re.search(r"fun recommendParams\(limit: Int = (\d+)\)", source)
    assert limit, "recommendParams must declare a default limit"
    assert int(limit.group(1)) <= 60, f"limit {limit.group(1)} is more than the grid shows"


def test_the_seed_card_shows_the_synopsis():
    """Tapping a title has to show what the title is about.

    The app already received the description on every result and displayed it
    nowhere, so the one field a reader most wants was the one field thrown
    away. This pins the views, the binding, and the clamp-plus-toggle, so a
    layout rename cannot quietly drop the synopsis again.
    """
    layout = ROOT / "android/app/src/main/res/layout/item_discover_header.xml"
    adapter = (
        ROOT / "android/app/src/main/java/com/mangabrain/app/ui/discover/DiscoverHeaderAdapter.kt"
    )
    markup = layout.read_text()
    kotlin = adapter.read_text()

    for view in ("seedDescription", "seedDescriptionToggle"):
        assert f'android:id="@+id/{view}"' in markup, f"{view} is missing from the layout"
        assert f"R.id.{view}" in kotlin, f"{view} is never bound"

    assert "seed.description" in kotlin, "the synopsis is never read from the response"
    # Clamped by default: a full AniList synopsis is long enough to push every
    # result off a phone screen.
    assert re.search(r"COLLAPSED_LINES\s*=\s*(\d+)", kotlin), "no collapsed line count"
    assert 'android:maxLines="4"' in markup


def test_the_client_needs_no_html_stripping():
    """The synopsis is displayed as plain text, so the server must send it so.

    description on MediaOut is description_clean, which has already had
    AniList's spoiler blocks, tags and entities removed. If that ever becomes
    the raw column, every synopsis in the app grows visible <br> markup.
    """
    assert "m.description_clean AS description" in (ROOT / "api/db.py").read_text()


def test_the_seed_card_shows_genres_and_tags():
    """Same facets, same order, same cap as the web client's seed card.

    The API sorts tags by AniList rank and caps them at 15, so a client cap
    decides which ranks a reader sees. Two clients capping differently would
    describe the same title differently, which is the kind of drift a second
    client exists to avoid.
    """
    layout = ROOT / "android/app/src/main/res/layout/item_discover_header.xml"
    adapter = (
        ROOT / "android/app/src/main/java/com/mangabrain/app/ui/discover/DiscoverHeaderAdapter.kt"
    )
    markup = layout.read_text()
    kotlin = adapter.read_text()
    javascript = (ROOT / "web/app.js").read_text()

    assert 'android:id="@+id/seedChips"' in markup, "the chip group is missing"
    assert "R.id.seedChips" in kotlin, "the chip group is never bound"
    assert "seed.genres" in kotlin and "seed.tags" in kotlin

    kotlin_cap = re.search(r"MAX_TAGS\s*=\s*(\d+)", kotlin)
    seed_card = javascript[javascript.index("function renderSeedCard(") :]
    web_cap = re.search(r"seed\.tags \|\| \[\]\)\.slice\(0, (\d+)\)", seed_card)
    assert kotlin_cap and web_cap, "both clients must cap the seed card's tags"
    assert kotlin_cap.group(1) == web_cap.group(1), (
        f"seed tag caps differ: Kotlin {kotlin_cap.group(1)}, web {web_cap.group(1)}"
    )


def test_tapping_a_facet_requires_it_the_way_the_web_chip_does():
    """A chip tap writes the same tri-state the filter panel writes.

    genreState/tagState hold "inc" or "exc" per name, and the parameter
    builders read that map. Writing anything else here would be a filter the
    panel cannot see and the user cannot clear.
    """
    fragment = ROOT / "android/app/src/main/java/com/mangabrain/app/ui/discover/DiscoverFragment.kt"
    source = fragment.read_text()
    assert "FilterState.genreState" in source and "FilterState.tagState" in source
    assert 'state[name] = "inc"' in source, "a chip tap must require, not exclude"
    assert "rerun()" in source, "requiring a facet must re-rank"


def test_the_app_shows_the_servers_own_explanation():
    """A status code alone is not actionable; this API's detail text is.

    409 is the clearest case: it means either "this seed has no embedding
    yet, run pipeline.embed" or "seeds span two embedding versions, a
    re-embed is in progress". Same code, different fix, and the phone used
    to show only "Server returned HTTP 409."
    """
    errors = ROOT / "android/app/src/main/java/com/mangabrain/app/data/api/ApiErrors.kt"
    assert errors.exists(), "ApiErrors.kt is missing"
    kotlin = errors.read_text()
    assert "JSONObject" in kotlin, "the detail must be parsed, not regexed"
    assert "error_http_detail" in kotlin

    strings = (ROOT / "android/app/src/main/res/values/strings.xml").read_text()
    assert 'name="error_http_detail"' in strings

    # Every screen that reports an HTTP failure has to use it, or one of
    # them silently drops the explanation - which is how this was missed.
    screens = [
        "ui/discover/DiscoverFragment.kt",
        "ui/settings/SettingsFragment.kt",
    ]
    for screen in screens:
        source = (ROOT / "android/app/src/main/java/com/mangabrain/app" / screen).read_text()
        assert "httpMessage(" in source, f"{screen} does not show the server's detail"


def test_every_api_error_carries_a_detail():
    """The client's fallback is the bare code, so a detail-less raise is a
    dead end on the phone. FastAPI allows HTTPException without one."""
    offenders = []
    for path in sorted((ROOT / "api").rglob("*.py")):
        source = path.read_text()
        for match in re.finditer(r"HTTPException\((.*?)\)", source, re.DOTALL):
            if "detail" not in match.group(1):
                offenders.append(f"{path.relative_to(ROOT)}: {match.group(1).strip()[:60]}")
    assert offenders == [], f"HTTPException raised without a detail: {offenders}"


def test_the_weight_defaults_agree_across_both_clients_and_the_server():
    """50 / 30 / 20 / 0 in three places, or the same request means two things.

    A slider default is not cosmetic: it is the weighting a reader gets
    before touching anything, so a client that starts at different numbers
    ranks the same catalog differently and neither screen says why.
    """
    kotlin = FILTER_STATE.read_text()
    markup = (ROOT / "web/index.html").read_text()

    for name, slider in (
        ("SEMANTIC", "wSemantic"),
        ("TAGS", "wTags"),
        ("GENRES", "wGenres"),
        ("TASTE", "wTaste"),
    ):
        android = re.search(rf"DEFAULT_W_{name} = (\d+)", kotlin)
        web = re.search(rf'id="{slider}"[^>]*value="(\d+)"', markup)
        assert android, f"FilterState has no DEFAULT_W_{name}"
        assert web, f"web/index.html has no default for {slider}"
        assert android.group(1) == web.group(1), (
            f"{slider} default differs: Android {android.group(1)}, web {web.group(1)}"
        )

    # The server's own defaults are the same numbers as fractions, so an
    # unweighted request from curl ranks like an untouched slider.
    params = {p["name"]: p for p in schema_parameters("/recommend/{media_id}")}
    for key, expected in (
        ("w_semantic", 0.5),
        ("w_tags", 0.3),
        ("w_genres", 0.2),
        ("w_taste", 0.0),
    ):
        assert params[key]["schema"].get("default") == expected, (
            f"server default for {key} is not {expected}"
        )


def test_reset_restores_the_weights():
    """Reset that leaves the weights is a button you cannot undo with.

    The sliders persist across restarts on Android - the web client reloads
    them from the markup every page load, which it gets for free - so
    without this there is no way back to 50/30/20 but dragging by eye.
    """
    source = FILTER_STATE.read_text()
    reset = source[source.index("fun reset()") :]
    reset = reset[: reset.index("\n    }")]
    for name in ("DEFAULT_W_SEMANTIC", "DEFAULT_W_TAGS", "DEFAULT_W_GENRES", "DEFAULT_W_TASTE"):
        assert name in reset, f"reset() does not restore {name}"


# The endpoints that receive FilterState.recommendParams(). The union check
# above cannot catch a parameter that one of them is missing, which is how
# /foryou came to ignore w_taste and cross_media from both clients.
RECOMMEND_PARAM_ENDPOINTS = ["/recommend/{media_id}", "/recommend", "/foryou"]


def kotlin_function_body(source: str, signature: str) -> str:
    """Source from a declaration to the next one.

    Not "up to the next closing brace": recommendParams is an expression
    body with no brace of its own, so that rule reads on into the function
    after it. The first version of this helper did, and reported `medium`
    (from searchParams) as a parameter /recommend ignores.
    """
    start = source.index(signature)
    rest = source[start + len(signature) :]
    end = rest.index("\n    fun ") if "\n    fun " in rest else len(rest)
    return rest[:end]


def recommend_param_keys() -> set[str]:
    """Every key the map sent to those endpoints can carry."""
    source = FILTER_STATE.read_text()
    keys: set[str] = set()
    for signature in ("fun filterParams()", "fun weightParams()", "fun recommendParams("):
        block = kotlin_function_body(source, signature)
        keys |= set(re.findall(r'"([a-z_]+)" to ', block))
        keys |= set(re.findall(r'params\["([a-z_]+)"\]', block))
    return keys


@pytest.mark.parametrize("path", RECOMMEND_PARAM_ENDPOINTS)
def test_every_endpoint_that_takes_the_weights_accepts_all_of_them(path):
    """One map goes to three endpoints, so all three must read all of it.

    FastAPI ignores an unknown query parameter in silence, so an endpoint
    missing one does not fail: the control just stops working on that
    screen with nothing to see. /foryou declared no w_taste and no
    cross_media while both clients sent them, which is exactly that.
    """
    missing = sorted(recommend_param_keys() - server_params(path))
    assert missing == [], f"{path} ignores parameters the client sends: {missing}"


def test_the_for_you_feed_sends_the_ratings_switch():
    """A switch that sets a field nobody transmits is a decoration.

    Settings › For you toggles ForYouPrefs.useRatings and /foryou reads
    use_ratings, but the request never carried it, so turning rating
    weighting off left the feed rating-weighted.
    """
    source = (
        ROOT / "android/app/src/main/java/com/mangabrain/app/ui/discover/DiscoverFragment.kt"
    ).read_text()
    assert 'params["use_ratings"]' in source, "the for-you request omits use_ratings"
    assert "use_ratings" in server_params("/foryou")


def test_rebuilding_the_filter_panel_does_not_stack_text_watchers():
    """Reset rebuilds the panel by calling onViewCreated on the same views.

    addTextChangedListener adds rather than replaces, so without removing
    the previous one first, every Reset left another watcher on all five
    fields - each firing on every keystroke for the life of the fragment.
    """
    source = (
        ROOT / "android/app/src/main/java/com/mangabrain/app/ui/filters/FiltersFragment.kt"
    ).read_text()
    assert "removeTextChangedListener" in source, (
        "bindTextFields adds a watcher without removing the previous one"
    )


def test_opening_a_title_starts_at_the_top_but_a_re_rank_does_not():
    """Both clients make the same distinction, for the same reason.

    Tapping a result replaces the whole page, so leaving the list where it
    was hid the new seed card above the fold. Moving a weight slider
    re-ranks the same seeds, and scrolling then would throw the reader out
    of the list they were reading. The web client compares the seed ids
    before scrolling; the Android one has to as well.
    """
    source = (
        ROOT / "android/app/src/main/java/com/mangabrain/app/ui/discover/DiscoverFragment.kt"
    ).read_text()
    javascript = (ROOT / "web/app.js").read_text()

    assert "scrollToPosition(0)" in source, "the grid never returns to the top"
    assert "shownSeedIds" in source and "shownSeedIds" in javascript, (
        "both clients track the seeds on screen to decide whether to scroll"
    )
    # The scroll must be guarded by that comparison, not unconditional.
    render = source[source.index("private fun render(") :]
    render = render[: render.index("\n    /**")]
    assert "if (ids != shownSeedIds)" in render, "a re-rank must not move the list"


def test_the_android_client_can_search_by_description():
    """The box, the call, and a parameter spelling the endpoint reads."""
    api = API_FILE.read_text()
    assert "fun describe(" in api, "no describe call on the API interface"
    block = api[api.index("fun describe(") : api.index("fun recommend(")]
    used = set(re.findall(r'@Query\("([^"]+)"\)', block))
    assert used - server_params("/describe") == set()

    layout = (ROOT / "android/app/src/main/res/layout/fragment_discover.xml").read_text()
    assert 'android:id="@+id/describeInput"' in layout

    fragment = (
        ROOT / "android/app/src/main/java/com/mangabrain/app/ui/discover/DiscoverFragment.kt"
    ).read_text()
    assert "R.id.describeInput" in fragment and "api.describe(" in fragment


def test_describe_sends_filters_but_not_weights():
    """A description has no seed, so the sliders have nothing to weigh.

    Sending them would not break anything - /describe ignores unknown
    parameters like every FastAPI route - but it would imply the sliders
    do something on that screen, which is the dead-control pattern this
    codebase keeps finding.
    """
    source = FILTER_STATE.read_text()
    block = kotlin_function_body(source, "fun describeParams(")
    assert "filterParams()" in block
    assert "weightParams()" not in block


def test_described_results_do_not_borrow_the_match_percentage():
    """A raw query-document cosine and a calibrated recommendation score
    are different measurements; showing both as "81%" makes them look like
    one."""
    grid = (ROOT / "android/app/src/main/java/com/mangabrain/app/ui/GridAdapter.kt").read_text()
    assert "val badge: String? = null" in grid, "no way to label a non-percentage result"

    fragment = (
        ROOT / "android/app/src/main/java/com/mangabrain/app/ui/discover/DiscoverFragment.kt"
    ).read_text()
    describe = fragment[fragment.index("private suspend fun runDescribe(") :]
    describe = describe[: describe.index("\n    fun loadRecommendations(")]
    assert "facet_coverage" in describe
    assert "similarity" not in describe, "a described row must not carry a match percentage"
