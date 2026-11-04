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
    from api.main import app

    return {p["name"] for p in app.openapi()["paths"][path]["get"].get("parameters", [])}


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
