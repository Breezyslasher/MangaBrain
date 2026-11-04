package com.mangabrain.app.data

import android.content.Context

/**
 * Server URL and optional access token.
 *
 * Plain SharedPreferences rather than EncryptedSharedPreferences: the token
 * is a shared secret for a self-hosted server, app-private storage already
 * keeps it from other apps on a non-rooted device, and androidx.security
 * is still an alpha dependency. Worth revisiting if this ever holds
 * anything with a blast radius beyond one MangaBrain instance.
 */
class Prefs(context: Context) {

    private val prefs = context.applicationContext
        .getSharedPreferences("mangabrain", Context.MODE_PRIVATE)

    /** Normalised with a trailing slash, which Retrofit's baseUrl requires. */
    var serverUrl: String?
        get() = prefs.getString(KEY_URL, null)?.takeIf { it.isNotBlank() }
        set(value) {
            val normalised = value?.trim()?.trimEnd('/')?.takeIf { it.isNotBlank() }
            prefs.edit().putString(KEY_URL, normalised?.plus("/")).apply()
        }

    var token: String?
        get() = prefs.getString(KEY_TOKEN, null)?.takeIf { it.isNotBlank() }
        set(value) = prefs.edit().putString(KEY_TOKEN, value?.trim()).apply()

    val isConfigured: Boolean
        get() = serverUrl != null

    private companion object {
        const val KEY_URL = "server_url"
        const val KEY_TOKEN = "access_token"
    }
}
