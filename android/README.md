# MangaBrain for Android

A native client for a self-hosted MangaBrain server. First slice: configure a
server, search the catalog, tap a title, see its recommendations.

It lives in this repository rather than its own so the client sits beside the
API it talks to. That is not filing tidiness - a second client duplicates
knowledge of the API, and keeping both here lets `tests/test_android_models.py`
check the Kotlin data classes against the FastAPI schema on every test run, so
a field added to `MediaOut` cannot silently go missing from the app.

## Build

Needs the Android SDK (API 34) and JDK 17.

    cd android
    ./gradlew assembleDebug        # app/build/outputs/apk/debug/

The Gradle wrapper is committed, so this needs no Gradle on the machine - just
a JDK and the SDK. Android Studio can also open the `android/` folder directly.

CI builds the same debug APK on every push that touches `android/`, and
uploads it as a workflow artifact, so an installable build is always one
download away without a local SDK.

## Installing over a previous build

Debug APKs are signed with the keystore committed at `app/debug.keystore`.
Without that, AGP signs with whatever `~/.android/debug.keystore` the build
machine happens to have, and since a fresh CI runner generates its own, two
builds of this app carried different certificates. Android refuses to update
across a certificate change (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`, which the
installer shows as the unhelpful "App not installed").

**One-time step:** any APK built before this keystore existed was signed with
a throwaway key, so it has to be uninstalled before the first build that uses
the committed one. After that, every build - local or CI - updates over the
last cleanly.

The keystore is not a secret. It carries the well-known Android debug
credentials (`androiddebugkey` / `android`), signs debug builds only, and
cannot publish to Play. Its entire purpose is that every build shares one
certificate. A real release key is the opposite case and belongs in CI
secrets.

## Configure

On first run the app opens Settings. Enter the server URL, for example
`http://192.168.1.10:8009` or `https://mangabrain.example.com`.

**The access token is optional and usually blank.** MangaBrain only enforces
auth when the server was started with `MANGABRAIN_AUTH_TOKEN` set:

```python
if is_api and settings.auth_token:   # default is "", so no auth
```

So a LAN server needs nothing here. Set the token only if you exposed the
server publicly (a Cloudflare tunnel, say) and configured one. Saving runs a
real request against `/search` and reports what happened, because a wrong
URL, a server that is down and a server that wants a token are otherwise
indistinguishable from the search screen.

Plain HTTP is permitted for private address ranges only
(`res/xml/network_security_config.xml`); a public host still has to be HTTPS,
so this cannot quietly downgrade an internet-facing server.

## Screens

Four tabs, matching the mobile design the web client was drawn for:

- **Discover** - medium chips, search, browse with filters, the seed card
  with its related titles, and a three-column result grid. Tapping a result
  makes it the seed, the same way the web client works, so drilling in keeps
  the medium and the filters.
- **Filters** - weight sliders, the filter fields, tri-state genre chips,
  tag require/exclude with autocomplete, and the three toggles. Nothing
  fires on change: the web client re-ranks live because a slider drag is
  cheap on a desktop connection, which on a phone would be a request per
  pixel, so edits are collected and Apply runs once.
- **Mix** - up to five seeds, with the For-you seed count and ratings
  weighting.
- **Lists** - the four sources, their sync buttons and per-list exclude
  toggles, plus keep-planned.

Usernames live on the SERVER, not the device: both clients read and write
GET/PUT /settings, so a list synced from either is excluded in both. Only
the server URL and token are local, because those describe how to reach the
server rather than what is in it.

## Known server-side gaps

- **MAL sync will fail.** Jikan discontinued its user list endpoints in
  2022. The button stays so the server's explanation is reachable.
- **Surprise me ignores tag filters.** /random accepts genres but declares
  no tag parameters, and FastAPI drops unknown query parameters silently.
  The client therefore does not send them, rather than appearing to.

## Deliberate omissions

No Hilt, no kapt, no ViewBinding, no Navigation component. Three screens do
not earn them, and annotation processing and code generation are the parts of
an Android build most likely to fail for reasons unrelated to the app.
Dependencies are wired by hand in `data/ServiceLocator.kt`. Add Hilt when the
screen count earns it.

The token is kept in plain `SharedPreferences` rather than
`EncryptedSharedPreferences`: app-private storage already keeps it from other
apps on a non-rooted device, and `androidx.security` is still alpha. Worth
revisiting if this ever holds a credential with a blast radius beyond one
MangaBrain instance.

## Scaffolding

Build configuration, the auth interceptor shape and the Retrofit/Moshi setup
follow `Breezyslasher/Yamtrack_andriod`, which is a known-good combination on
this toolchain. The screens share nothing with it - that app tracks media,
this one recommends it.
