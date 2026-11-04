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

## What it does not do yet

The weight sliders, the filter panel, the seed mix, random discovery, and
list exclusion are all server features this client does not expose. It covers
the path that proves the whole chain works - auth, networking, models, two
screens - and nothing more.

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
