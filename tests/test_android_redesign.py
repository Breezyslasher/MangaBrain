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
        # Profiles (api/profiles.py) came after the redesign spec.
        "PROFILE",
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


def test_one_account_template_serves_every_list():
    accounts = re.search(r"enum class Account\((.*?)\n    \}", SETTINGS, re.S).group(0)
    for logo, bg in (
        ("logo_anilist", "brand_anilist"),
        ("logo_myanimelist", "brand_mal"),
        ("logo_kitsu", "brand_kitsu"),
        ("logo_yamtrack", "brand_yamtrack"),
        ("logo_hardcover", "brand_hardcover"),
    ):
        assert f"R.drawable.{logo}, R.color.{bg}" in accounts, logo
        assert f'name="{bg}"' in COLORS, bg
    assert SETTINGS.count("private fun buildAccount(") == 1


def test_each_list_shows_its_logo_with_its_licence():
    drawables = ROOT / "res"
    for name in ("logo_anilist", "logo_myanimelist", "logo_kitsu"):
        vector = (drawables / f"drawable/{name}.xml").read_text()
        assert "android:pathData=" in vector and "Simple Icons" in vector, name
    png = (drawables / "drawable-nodpi/logo_yamtrack.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    licenses = ROOT.parent.parent.parent / "licenses"
    assert "CC0" in (licenses / "LICENSE-SimpleIcons.txt").read_text()
    assert "Affero" in (licenses / "NOTICE-Yamtrack.txt").read_text()
    hardcover = (drawables / "drawable/logo_hardcover.xml").read_text()
    assert "android:pathData=" in hardcover and "hardcover-docs" in hardcover
    assert "MIT License" in (licenses / "LICENSE-HardcoverDocs.txt").read_text()
    assert "initialsBadge" not in SETTINGS


def test_saving_one_account_leaves_the_others_alone():
    """AppSettingsIn's nulls are omitted from the body, so each branch
    sets exactly one account's field."""
    block = SETTINGS[SETTINGS.index("val body = when (a) {") :]
    block = block[: block.index("\n        }")]
    assert block.count("AppSettingsIn(") == 5
    for line in block.splitlines()[1:]:
        fields = (
            line.count("_username =")
            + line.count("yamtrack_url =")
            + line.count("hardcover_token =")
        )
        assert fields == 1, line


def test_hardcover_is_connected_by_its_token_and_excludable():
    assert "if (a == Account.HARDCOVER) hardcoverTokenSet" in SETTINGS
    assert "api.refreshHardcover()" in SETTINGS and "api.hardcoverStatus()" in SETTINGS
    filters = (JAVA / "data/FilterState.kt").read_text()
    assert 'if (excludeHardcover) add("hardcover")' in filters
    assert '"ex_hardcover"' in filters


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


def test_the_medium_is_a_dropdown_on_both_clients():
    """Seven media no longer fit a row of chips on a phone; the last ones
    scrolled off where nobody saw them."""
    discover = (JAVA / "ui/discover/DiscoverFragment.kt").read_text()
    layout = (ROOT / "res/layout/fragment_discover.xml").read_text()
    assert 'android:id="@+id/mediumSpinner"' in layout and "mediumChips" not in layout
    assert "fun buildMediumSpinner(spinner: Spinner)" in discover
    assert "if (key == FilterState.medium) return" in discover, "a rebuild must not rerun"
    web = ROOT.parents[3] / "web"
    assert '<select id="tabs"' in (web / "index.html").read_text()
    assert '$("tabs").addEventListener("change"' in (web / "app.js").read_text()


def test_a_recycled_card_without_a_cover_cancels_the_previous_load():
    # Otherwise the previous title's cover finishes loading into the card
    # and shows under this title's name.
    helper = (JAVA / "ui/CoverImage.kt").read_text()
    assert "dispose()" in helper.split("isNullOrBlank()")[1].split("else")[0]
    for path in ("ui/GridAdapter.kt", "ui/MixAdapter.kt", "ui/discover/DiscoverHeaderAdapter.kt"):
        source = (JAVA / path).read_text()
        assert "cover.showCover(url)" in source, path
        assert "cover.load(" not in source, path
