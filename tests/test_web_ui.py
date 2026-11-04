"""Invariants the mobile redesign must not break.

The redesign moved almost every control in web/index.html into new
wrappers, panes and a bottom sheet. Two things make that safe to do, and
neither is visible in a diff:

  1. app.js binds by id. A renamed or dropped id does not raise - the
     binding helper logs a warning and the button is simply dead - so the
     id set is checked here instead.
  2. Desktop was explicitly out of scope. It stays intact because the new
     layout lives inside @media (max-width: 760px) and the two tokens the
     redesign reuses with different values are redefined there rather than
     in :root.

Layout itself is verified in a browser, not here; these are the cheap
structural facts that a later edit could quietly undo.
"""

import json
import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
HTML = (WEB / "index.html").read_text()
JS = (WEB / "app.js").read_text()
CSS = (WEB / "style.css").read_text()

HTML_IDS = set(re.findall(r'id="([A-Za-z0-9_-]+)"', HTML))
# $("x") for lookups, on("x", ...) for event binding. Both take literals.
BOUND_IDS = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', JS)) | set(
    re.findall(r'\bon\("([A-Za-z0-9_-]+)"', JS)
)

BREAKPOINT = "@media (max-width: 760px) {"


def mobile_block() -> str:
    """The text of the mobile media query, brace-matched."""
    start = CSS.index(BREAKPOINT)
    depth = 0
    for i in range(start + len(BREAKPOINT) - 1, len(CSS)):
        if CSS[i] == "{":
            depth += 1
        elif CSS[i] == "}":
            depth -= 1
            if depth == 0:
                return CSS[start : i + 1]
    raise AssertionError("unbalanced braces in the mobile media query")


def test_every_id_app_js_binds_to_exists():
    """A dead button is the failure mode, not an exception.

    `on()` warns and carries on when an element is missing, deliberately, so
    that a stale cached index.html cannot kill the rest of startup. The cost
    is that a typo or a dropped id in a redesign is silent.
    """
    missing = sorted(BOUND_IDS - HTML_IDS)
    assert missing == [], missing


def test_the_controls_the_redesign_rearranged_are_all_still_present():
    """Named explicitly, because these are the ones that moved.

    Each was lifted into a pane, a sheet or an account card. The id test
    above only covers what app.js references by literal; these are listed
    again so a drop is caught by name rather than by absence.
    """
    for element in [
        "wSemantic",
        "wTags",
        "wGenres",
        "wTaste",
        "yearMin",
        "yearMax",
        "minScore",
        "format",
        "country",
        "status",
        "titleLang",
        "maxPop",
        "minLen",
        "maxLen",
        "dubLang",
        "dubNote",
        "genreChips",
        "tagInput",
        "tagSuggest",
        "tagReq",
        "tagExc",
        "tagChips",
        "adult",
        "crossMedia",
        "excludeFranchise",
        "mixBlock",
        "mixChips",
        "mixGo",
        "alUser",
        "malUser",
        "kitsuUser",
        "ytUrl",
        "ytToken",
        "alRefresh",
        "malRefresh",
        "kitsuRefresh",
        "ytRefresh",
        "alExclude",
        "malExclude",
        "kitsuExclude",
        "ytExclude",
        "alStatus",
        "malStatus",
        "kitsuStatus",
        "ytStatus",
        "keepPlanned",
        "acctSave",
        "acctStatus",
        "forYouSeeds",
        "useRatings",
        "forYou",
        "surprise",
        "search",
        "browseBtn",
        "seed",
        "related",
        "results",
        "statusMsg",
        "tabs",
        "panel",
        "content",
        "formatWrap",
        "dubbedRow",
    ]:
        assert element in HTML_IDS, element


def test_the_mobile_shell_exists():
    for element in [
        "mobileNav",
        "filterSheet",
        "sheetBody",
        "sheetFooter",
        "sheetScrim",
        "filterBtn",
        "filterCount",
        "resultsBar",
        "mixPane",
        "listsPane",
        "discoverActions",
        "forYouCard",
    ]:
        assert element in HTML_IDS, element
    # Four tabs, in the order the design shows them. Scoped to the nav: the
    # body carries data-pane too, as the current pane.
    nav = HTML[
        HTML.index('<nav id="mobileNav"') : HTML.index("</nav>", HTML.index('<nav id="mobileNav"'))
    ]
    assert re.findall(r'data-pane="([a-z]+)"', nav) == [
        "discover",
        "filters",
        "mix",
        "lists",
    ]


@pytest.mark.parametrize(
    "token",
    [
        "--surface",
        "--field",
        "--border-2",
        "--track",
        "--text-2",
        "--dim",
        "--w-synopsis",
        "--w-tags",
        "--w-genres",
        "--w-taste",
        "--ok",
        "--ok-bg",
        "--bad",
        "--bad-bg",
        "--r-sm",
        "--r",
        "--r-lg",
        "--r-xl",
        "--pill",
        "--font",
    ],
)
def test_redesign_tokens_are_declared_in_root(token):
    root = CSS[CSS.index(":root {") : CSS.index("}", CSS.index(":root {"))]
    assert f"{token}:" in root, token


def test_the_two_clashing_tokens_are_scoped_to_the_breakpoint():
    """This is what keeps desktop intact.

    The redesign reuses --bg and --border with different values (#0e1015 and
    #232a3a) than the ones every desktop rule already reads (#12141a and
    #2b3245). Declaring those in :root would restyle the desktop layout that
    was explicitly out of scope, so they are redefined inside the media
    query instead.
    """
    root = CSS[CSS.index(":root {") : CSS.index("}", CSS.index(":root {"))]
    assert "--bg: #12141a" in root
    assert "--border: #2b3245" in root
    mobile = mobile_block()
    assert "--bg: #0e1015" in mobile
    assert "--border: #232a3a" in mobile


def test_the_breakpoint_matches_the_spec():
    assert BREAKPOINT in CSS
    # The pre-existing single-column breakpoint must survive alongside it.
    assert "@media (max-width: 900px)" in CSS


def test_pane_and_sheet_layout_is_confined_to_the_breakpoint():
    """No pane rule may leak into the desktop cascade."""
    mobile = mobile_block()
    outside = CSS.replace(mobile, "")
    for rule in ["body[data-pane=", ".sheet-open", "#mobileNav {"]:
        assert rule not in outside, rule
        assert rule in mobile, rule


def test_theme_colour_is_set_in_both_places():
    manifest = json.loads((WEB / "manifest.json").read_text())
    assert manifest["theme_color"] == "#0e1015"
    assert '<meta name="theme-color" content="#0e1015">' in HTML


def test_inputs_are_at_least_16px_so_ios_does_not_zoom():
    """Every font-size on an input inside the breakpoint, checked.

    Below 16px iOS Safari zooms the page on focus and the user is left
    scrolled sideways in a layout that was sized to the viewport.
    """
    mobile = mobile_block()
    # Blocks that style an input or select, with their font-size.
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", mobile):
        if "input" not in selector and "select" not in selector:
            continue
        for size in re.findall(r"font-size:\s*([\d.]+)px", body):
            assert float(size) >= 16, f"{selector.strip()} -> {size}px"
