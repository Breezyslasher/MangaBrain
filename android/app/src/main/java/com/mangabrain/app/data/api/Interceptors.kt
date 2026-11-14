package com.mangabrain.app.data.api

import okhttp3.Interceptor
import okhttp3.Response

/** Supplies the current token; null or blank means the server has no auth. */
interface TokenProvider {
    fun getToken(): String?
    fun setToken(token: String?)

    /** The profile to send, or null for the default, which sends none. */
    fun getProfile(): String? = null
    fun setProfile(profile: String?) = Unit
}

class InMemoryTokenProvider : TokenProvider {
    @Volatile
    private var token: String? = null

    @Volatile
    private var profile: String? = null

    override fun getToken(): String? = token

    override fun setToken(token: String?) {
        this.token = token
    }

    override fun getProfile(): String? = profile

    override fun setProfile(profile: String?) {
        this.profile = profile
    }
}

/**
 * Adds the bearer token, and ONLY when there is one.
 *
 * MangaBrain's auth is opt-in: api/main.py guards the check with
 * `if is_api and settings.auth_token:`, and auth_token defaults to "". So a
 * LAN server normally has no auth at all, and sending an empty
 * `Authorization: Bearer` header would be noise at best. The token exists
 * for servers exposed publicly, where it is set deliberately.
 */
class AuthInterceptor(private val tokenProvider: TokenProvider) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val builder = chain.request().newBuilder().header("Accept", "application/json")
        val token = tokenProvider.getToken()
        if (!token.isNullOrBlank()) {
            builder.header("Authorization", "Bearer $token")
        }
        // Whose accounts and lists (api/profiles.py). The default sends
        // nothing, exactly as before profiles existed.
        val profile = tokenProvider.getProfile()
        if (!profile.isNullOrBlank()) {
            builder.header("X-Profile", profile)
        }
        return chain.proceed(builder.build())
    }
}
