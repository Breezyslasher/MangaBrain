"""The settings page, held the same in both clients.

Both clients build the media section from GET /datasets rather than from
a list of their own, so a medium added on the server needs no client
change. These pin that, and the parts where the two could drift: the tab
labels, asking before a download, and the selection hiding tabs.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_HTML = (ROOT / "web/index.html").read_text()
WEB_JS = (ROOT / "web/app.js").read_text()
ANDROID = ROOT / "android/app/src/main/java/com/mangabrain/app"
FILTER_STATE = (ANDROID / "data/FilterState.kt").read_text()
SETTINGS = (ANDROID / "ui/settings/SettingsFragment.kt").read_text()
DISCOVER = (ANDROID / "ui/discover/DiscoverFragment.kt").read_text()


def web_tabs() -> dict[str, str]:
    return dict(re.findall(r'<option data-medium="(\w+)"[^>]*>([^<]+)</option>', WEB_HTML))


def android_tabs() -> dict[str, str]:
    block = FILTER_STATE[FILTER_STATE.index("MEDIUM_LABELS") :]
    block = block[: block.index(")\n")]
    return dict(re.findall(r'"(\w+)" to "([^"]+)"', block))


def test_both_clients_label_every_medium_the_same():
    from api.media_groups import MEDIUM_GROUPS

    assert web_tabs() == android_tabs()
    assert list(android_tabs()) == list(MEDIUM_GROUPS), "same keys, same order as the API"


def test_both_clients_build_the_media_section_from_the_server():
    assert 'api("/datasets")' in WEB_JS
    assert "api.datasets()" in SETTINGS
    # Neither names a dataset; both read the media and cost from the reply.
    for source in (WEB_JS, SETTINGS):
        assert "dataset.media" in source and "download_hint" in source
        assert '"books"' not in source and '"openlibrary"' not in source


def test_both_clients_ask_before_a_download():
    assert "window.confirm(`Download ${dataset.label}?" in WEB_JS
    assert "media_download_title" in SETTINGS and "AlertDialog.Builder" in SETTINGS


def test_both_clients_hide_tabs_the_selection_leaves_out():
    assert "const off = !media.includes(option.dataset.medium);" in WEB_JS
    assert "option.hidden = off;" in WEB_JS
    assert "FilterState.visibleMedia()" in DISCOVER
    assert "enabledMedia = settings.enabled_media.orEmpty()" in FILTER_STATE


def test_a_medium_switched_off_leaves_the_android_dropdown_at_once():
    """On the web a hidden medium is only left when nothing is on screen,
    so a shared link into it still opens. Android has no such links, and
    keeping a switched-off medium listed while it was in use read as the
    setting not having worked, so the app leaves it straight away."""
    assert "!media.includes(state.medium) && !state.mode" in WEB_JS
    assert "if (FilterState.medium !in visible) {" in DISCOVER
    assert "fun mediaChanged()" in DISCOVER and "discoverFragment()?.mediaChanged()" in SETTINGS


def test_the_android_tabs_follow_changes_made_in_settings():
    assert "override fun onResume()" in DISCOVER and "refreshMedia()" in DISCOVER
    assert "override fun onHiddenChanged" in DISCOVER


def test_the_account_fields_moved_to_the_web_settings_page():
    dialog = WEB_HTML[WEB_HTML.index('<dialog id="settingsDialog"') : WEB_HTML.index("</dialog>")]
    for field in ("alUser", "malUser", "kitsuUser", "ytUrl", "ytToken", "acctSave"):
        assert f'id="{field}"' in dialog, field
        assert WEB_HTML.count(f'id="{field}"') == 1, f"{field} exists once"
    # Per-search controls stay in the panel.
    panel = WEB_HTML[WEB_HTML.index('<aside id="panel">') : WEB_HTML.index("</aside>")]
    for field in ("alRefresh", "malExclude", "keepPlanned"):
        assert f'id="{field}"' in panel, field


def test_the_web_stops_polling_when_settings_close():
    assert 'on("settingsDialog", "close"' in WEB_JS
    assert '$("settingsDialog").open' in WEB_JS
