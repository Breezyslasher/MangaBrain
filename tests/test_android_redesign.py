"""The Android redesign (mobile 2a, Settings 3b), held to the spec.

No emulator runs in this suite, so these read the sources the way the
model checks do. They pin the decisions a later edit could quietly undo:
the tab order and icons, the back button, the tab bar hiding on detail
pages, one account template for all four lists, and where each control
now lives.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "android/app/src/main"
JAVA = ROOT / "java/com/mangabrain/app"
SETTINGS = (JAVA / "ui/settings/SettingsFragment.kt").read_text()
MAIN = (JAVA / "ui/MainActivity.kt").read_text()
MENU = (ROOT / "res/menu/bottom_nav.xml").read_text()
THEME = (ROOT / "res/values/themes.xml").read_text()
COLORS = (ROOT / "res/values/colors.xml").read_text()


def test_the_tab_bar_is_discover_filters_mix_settings_with_mdi_icons():
    items = re.findall(r'android:icon="@drawable/(\w+)" android:title="@string/(\w+)"', MENU)
    assert items == [
        ("mdi_compass_outline", "tab_discover"),
        ("mdi_tune_variant", "tab_filters"),
        ("mdi_shuffle_variant", "tab_mix"),
        ("mdi_cog", "tab_settings"),
    ]


def test_every_icon_is_a_real_mdi_vector():
    for name in re.findall(r"R\.drawable\.(mdi_\w+)", SETTINGS) + re.findall(
        r"@drawable/(mdi_\w+)", MENU
    ):
        icon = ROOT / f"res/drawable/{name}.xml"
        assert icon.exists(), name
        text = icon.read_text()
        assert 'android:viewportWidth="24"' in text and "android:pathData=" in text, name


def test_the_active_tab_has_the_pill():
    pill = THEME[THEME.index('name="Widget.MangaBrain.BottomNav.Pill"') :]
    pill = pill[: pill.index("</style>")]
    assert '<item name="android:width">60dp</item>' in pill
    assert '<item name="android:height">30dp</item>' in pill
    assert "@color/accent_bg" in pill
    assert '<item name="android:textSize">11.5sp</item>' in THEME


def test_the_spec_tokens_are_defined():
    for name, value in {
        "bg": "#0E1015",
        "surface": "#141821",
        "field": "#1A1E27",
        "border": "#232A3A",
        "border_2": "#2B3245",
        "track": "#262C3C",
        "accent": "#5B8DEF",
        "ok": "#37C9A5",
        "bad": "#E05C6E",
        "surface_hi": "#1F2533",
        "accent_bg": "#1D2B44",
        "accent_text": "#8FB0F5",
        "nav_handle": "#5A6176",
    }.items():
        assert f'<color name="{name}">{value}</color>' in COLORS, name


def test_the_fonts_are_bundled_with_their_licences():
    for font in (
        "space_grotesk_regular",
        "space_grotesk_medium",
        "space_grotesk_bold",
        "jetbrains_mono_medium",
        "jetbrains_mono_bold",
    ):
        data = (ROOT / f"res/font/{font}.ttf").read_bytes()
        assert data[:4] in (b"\x00\x01\x00\x00", b"true"), f"{font} is not a TrueType file"
    licences = ROOT.parent.parent.parent / "licenses"
    for name in (
        "OFL-SpaceGrotesk.txt",
        "OFL-JetBrainsMono.txt",
        "LICENSE-MaterialDesignIcons.txt",
    ):
        assert (licences / name).exists(), name
    assert "@font/space_grotesk" in THEME


def test_settings_replaces_lists_and_the_server_screen():
    assert not (JAVA / "ui/lists/ListsFragment.kt").exists()
    assert not (JAVA / "ui/SettingsActivity.kt").exists()
    assert "SettingsActivity" not in (ROOT / "AndroidManifest.xml").read_text()
    # First run opens Settings › Server instead of a separate activity.
    assert "settings.openServer()" in MAIN


def test_every_page_in_the_spec_exists():
    pages = re.search(r"enum class Page \{([^}]*)\}", SETTINGS).group(1)
    assert [p.strip() for p in pages.split(",")] == [
        "HOME",
        "ACCOUNTS",
        "ACCOUNT",
        "EXCLUDE",
        "FOR_YOU",
        "MEDIA",
        "DISPLAY",
        "SERVER",
    ]


def test_the_back_button_pops_settings_pages_and_only_while_visible():
    assert "onBackPressedDispatcher.addCallback(viewLifecycleOwner, back)" in SETTINGS
    assert "back.isEnabled = !isHidden && stack.size > 1" in SETTINGS


def test_the_tab_bar_hides_on_detail_pages():
    assert "setTabBarVisible(isHidden || top == Page.HOME)" in SETTINGS
    assert "fun setTabBarVisible(visible: Boolean)" in MAIN


def test_one_account_template_serves_all_four_lists():
    accounts = re.search(r"enum class Account\((.*?)\n    \}", SETTINGS, re.S).group(0)
    for initials, bg, fg in (
        ("AL", "accent_bg", "accent_text"),
        ("MAL", "badge_mal_bg", "badge_mal_fg"),
        ("KT", "badge_kitsu_bg", "badge_kitsu_fg"),
        ("YT", "ok_bg", "ok"),
    ):
        assert f'"{initials}", R.color.{bg}, R.color.{fg}' in accounts, initials
    assert SETTINGS.count("private fun buildAccount(") == 1


def test_saving_one_account_leaves_the_other_three_alone():
    """AppSettingsIn's nulls are omitted from the body, so each branch
    sets exactly one account's field."""
    block = SETTINGS[SETTINGS.index("val body = when (a) {") :]
    block = block[: block.index("\n        }")]
    assert block.count("AppSettingsIn(") == 4
    for line in block.splitlines()[1:]:
        assert line.count("_username =") + line.count("yamtrack_url =") == 1, line


def test_for_you_lives_in_settings_not_mix():
    mix = (JAVA / "ui/mix/MixFragment.kt").read_text()
    assert "ForYouPrefs" not in mix
    assert "seedsDown" not in (ROOT / "res/layout/fragment_mix.xml").read_text()
    assert "ForYouPrefs.seeds" in SETTINGS and "ForYouPrefs.useRatings" in SETTINGS


def test_display_sets_the_title_language_the_titles_read():
    models = (JAVA / "data/api/Models.kt").read_text()
    assert "FilterState.romajiTitles" in models
    state = (JAVA / "data/FilterState.kt").read_text()
    assert '.putBoolean("romaji_titles", romajiTitles)' in state
    assert 'romajiTitles = prefs.getBoolean("romaji_titles", false)' in state
    assert "discoverFragment()?.refreshTitles()" in SETTINGS


def test_no_field_is_made_single_line_after_its_input_type():
    """setSingleLine() swaps the transformation method, which shows a
    password field's text. The token fields must not go through it."""
    code = [line for line in SETTINGS.splitlines() if not line.strip().startswith("//")]
    assert not any("setSingleLine" in line for line in code)


def test_a_list_never_synced_is_not_an_error():
    block = SETTINGS[SETTINGS.index("private suspend fun fetchStatus") :]
    block = block[: block.index("\n    }\n")]
    assert "catch (e: Exception)" in block and "null" in block


def test_statuses_wait_for_the_usernames():
    assert "if (statusesLoaded || !settingsLoaded) return" in SETTINGS


def test_the_media_page_keeps_polling_only_while_shown():
    assert "if (!active || isHidden || stack.last() != Page.MEDIA) break" in SETTINGS


def test_every_android_resource_is_well_formed_xml():
    """aapt refuses a malformed resource before Kotlin is even compiled,
    and the first redesign build died on '--' inside a comment, which XML
    forbids. A strict parse here catches that class of mistake in a second
    rather than a CI round."""
    import xml.dom.minidom

    for path in sorted((ROOT / "res").rglob("*.xml")):
        xml.dom.minidom.parse(str(path))
