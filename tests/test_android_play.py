"""The Android release build is the Google Play build: release-signed from
secrets, a growing version code and a recent target SDK."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRADLE = (ROOT / "android/app/build.gradle").read_text()
RELEASE = (ROOT / ".github/workflows/android-release.yml").read_text()
APP = ROOT / "android/app/src/main"


def test_release_signing_and_version_come_from_the_environment():
    assert 'System.getenv("MANGABRAIN_KEYSTORE_FILE")' in GRADLE
    assert "hasReleaseKey ? signingConfigs.release : signingConfigs.debug" in GRADLE
    assert 'System.getenv("MANGABRAIN_VERSION_CODE")' in GRADLE
    assert "targetSdk 36" in GRADLE and "compileSdk 36" in GRADLE
    assert "./gradlew bundleRelease" in RELEASE
    assert 'grep -q "CN=Android Debug"' in RELEASE, "a debug-signed bundle fails the job"


def test_the_screens_pad_themselves_for_the_system_bars():
    """Targeting 36 draws the app under the bars with no opt-out; the
    activity pads for the bars and the keyboard."""
    main = (APP / "java/com/mangabrain/app/ui/MainActivity.kt").read_text()
    assert "padForSystemBars()" in main
    assert "WindowInsetsCompat.Type.ime()" in main
    assert "WindowInsetsCompat.Type.systemBars()" in main
    assert not (APP / "res/values-v35").exists(), "the API 35 opt-out is gone"
    layout = (APP / "res/layout/activity_main.xml").read_text()
    assert "fitsSystemWindows" not in layout, "the activity pads, not the layout"


def test_the_build_tools_can_target_36():
    root = (ROOT / "android/build.gradle").read_text()
    assert "id 'com.android.application' version '8.11.1'" in root
    wrapper = (ROOT / "android/gradle/wrapper/gradle-wrapper.properties").read_text()
    assert "gradle-8.13-bin.zip" in wrapper


def test_plain_http_to_a_lan_server_is_allowed():
    """Domain entries never matched an address range, so http to a LAN IP
    was refused."""
    config = (APP / "res/xml/network_security_config.xml").read_text()
    assert '<base-config cleartextTrafficPermitted="true">' in config
    assert "192.168.0.0" not in config.split("-->")[-1]


def test_the_play_identity_is_set_and_the_code_package_is_not_moved():
    assert 'applicationId "io.github.breezyslasher.mangabrain"' in GRADLE
    assert "namespace 'com.mangabrain.app'" in GRADLE
