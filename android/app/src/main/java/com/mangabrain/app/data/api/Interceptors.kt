package com.mangabrain.app.data.api

import okhttp3.Interceptor
import okhttp3.Response

/** Supplies the current token; null or blank means the server has no auth. */
interface TokenProvider {
    fun getToken(): String?
    fun setToken(token: String?)
}

class InMemoryTokenProvider : TokenProvider {
    @Volatile
    private var token: String? = null

    override fun getToken(): String? = token

    override fun setToken(token: String?) {
        this.token = token
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
        return chain.proceed(builder.build())
    }
}
