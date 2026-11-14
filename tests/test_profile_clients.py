"""Both clients send the profile they use with every request and let the
reader pick, make and remove profiles."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "web/app.js").read_text()
MOBILE_JS = (ROOT / "web/mobile.js").read_text()
HTML = (ROOT / "web/index.html").read_text()


def test_web_sends_the_profile_with_every_request():
    headers = APP_JS.split("function authHeaders()")[1].split("\n}\n")[0]
    assert 'headers["X-Profile"] = profile' in headers
    assert 'profile !== "default"' in headers, "the default sends nothing, as before"


def test_web_has_a_profile_picker_on_both_layouts():
    assert 'id="profileSelect"' in HTML and 'id="profileAdd"' in HTML
    assert "profile: renderProfile" in MOBILE_JS
    assert '"Profile",' in MOBILE_JS


def test_a_profile_removed_elsewhere_falls_back_to_the_default():
    body = APP_JS.split("async function loadProfiles()")[1].split("\n}\n")[0]
    assert "!data.profiles.includes(currentProfile())" in body
    assert 'useProfile("default")' in body
