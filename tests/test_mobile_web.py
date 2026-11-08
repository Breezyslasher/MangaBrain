"""The phone layout: the Android app's tabs and Settings pages, on the web.

mobile.css applies only under the media query and mobile.js does nothing
above it, so the desktop is untouched. On a phone, mobile.js moves the
desktop's own controls into the app's pages instead of rebuilding them, so
every search, filter and sync still runs through app.js.

The layout itself was checked in a browser at phone and desktop widths;
these tests hold the structure that keeps it safe.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
HTML = (WEB / "index.html").read_text()
APP = (WEB / "app.js").read_text()
MOBILE = (WEB / "mobile.js").read_text()
CSS = (WEB / "mobile.css").read_text()


def test_the_phone_sheet_applies_only_under_the_query():
    assert '<link rel="stylesheet" href="mobile.css" media="(max-width: 700px)">' in HTML
    assert 'const MOBILE_QUERY = "(max-width: 700px)"' in MOBILE


def test_mobile_js_loads_after_app_js():
    assert HTML.index('src="app.js"') < HTML.index('src="mobile.js"')


def test_every_phone_rule_is_scoped_to_the_mobile_layout():
    """A stray global rule would be harmless on desktop (the sheet does not
    apply there) but must not restyle the hidden desktop page either."""
    for selector in re.findall(r"^([^@\s/{}][^{]*)\{", CSS, re.M):
        for part in selector.split(","):
            part = part.strip()
            if not part or part.startswith(("from", "to")):
                continue
            assert part.startswith(("body.m-on", ".m-")), part


def test_the_app_tabs_and_settings_pages_exist():
    for tab in (
        '["discover", "Discover"]',
        '["filters", "Filters"]',
        '["mix", "Mix"]',
        '["settings", "Settings"]',
    ):
        assert tab in MOBILE
    for page in ("home", "accounts", "account", "exclude", "foryou", "media", "display", "server"):
        assert f"{page}: render" in MOBILE


def test_moved_controls_are_never_thrown_away():
    """A Settings page holds desktop controls (#alUser, the exclude boxes,
    the dataset list); redrawing it must put them back first, or app.js
    finds nothing where they used to be."""
    render = MOBILE.split("function renderSettings()")[1].split("\n  }\n")[0]
    assert render.index("rescue(page)") < render.index("page.replaceChildren()")
    leave = MOBILE.split("function leave()")[1].split("\n  }\n")[0]
    assert "restoreAll()" in leave


def test_the_desktop_functions_it_calls_exist():
    for name in (
        "renderMix",
        "addToMix",
        "loadMix",
        "rerunActive",
        "renderGenreChips",
        "renderTagChips",
        "renderMediaSettings",
        "applyEnabledMedia",
        "authFetch",
        "mediaTitle",
    ):
        assert re.search(rf"^(async )?function {name}\(", APP, re.M), name
    for element_id in (
        "tabs",
        "search",
        "browseBtn",
        "describe",
        "describeBtn",
        "statusMsg",
        "seed",
        "related",
        "results",
        "forYou",
        "surprise",
        "alUser",
        "malUser",
        "kitsuUser",
        "ytUrl",
        "ytToken",
        "alExclude",
        "malExclude",
        "kitsuExclude",
        "ytExclude",
        "keepPlanned",
        "forYouSeeds",
        "useRatings",
        "titleLang",
        "mediaSettings",
        "mediaStatus",
        "mixGo",
    ):
        assert f'id="{element_id}"' in HTML, element_id


def test_hardcover_exclusion_reaches_the_query_only_when_the_box_exists():
    assert 'if (isChecked("hcExclude")) excludeLists.push("hardcover");' in APP


def test_nothing_from_the_server_is_written_as_html():
    assert "innerHTML" not in MOBILE


def test_fonts_and_logo_ship_with_the_page():
    for name in (
        "fonts/space_grotesk_regular.ttf",
        "fonts/space_grotesk_medium.ttf",
        "fonts/space_grotesk_bold.ttf",
        "fonts/jetbrains_mono_medium.ttf",
        "fonts/jetbrains_mono_bold.ttf",
        "img/logo_yamtrack.png",
    ):
        assert (WEB / name).is_file(), name
        assert name in CSS or name in MOBILE


def test_the_phone_assets_are_versioned():
    from api.main import _versioned_index

    html = _versioned_index(WEB, WEB / "index.html")
    assert 'href="mobile.css?v=' in html
    assert 'src="mobile.js?v=' in html
