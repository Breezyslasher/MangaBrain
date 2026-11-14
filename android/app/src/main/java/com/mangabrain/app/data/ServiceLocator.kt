package com.mangabrain.app.data

import android.content.Context
import com.mangabrain.app.data.api.AuthInterceptor
import com.mangabrain.app.data.api.InMemoryTokenProvider
import com.mangabrain.app.data.api.MangaBrainApi
import com.mangabrain.app.data.api.TokenProvider
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.moshi.MoshiConverterFactory
import java.util.concurrent.TimeUnit

/**
 * Manual dependency wiring. Three screens do not earn Hilt, and dropping it
 * drops kapt with it - annotation processing is the part of an Android build
 * most likely to fail for reasons unrelated to the app.
 *
 * The Retrofit instance is rebuilt when the server URL changes rather than
 * being pinned to a placeholder base URL and rewritten per request by an
 * interceptor. A self-hosted app has no sensible default server, so until
 * one is configured there is nothing to build and [api] returns null; the
 * UI sends the user to Settings instead of failing a request.
 */
object ServiceLocator {

    private val tokenProvider: TokenProvider = InMemoryTokenProvider()

    @Volatile
    private var cachedUrl: String? = null

    @Volatile
    private var cachedApi: MangaBrainApi? = null

    @Synchronized
    fun api(context: Context): MangaBrainApi? {
        val prefs = Prefs(context)
        val url = prefs.serverUrl ?: return null
        // Read every time: the token can be set in Settings between calls,
        // and the interceptor holds only a reference to the provider.
        tokenProvider.setToken(prefs.token)
        tokenProvider.setProfile(prefs.profile.takeIf { it != DEFAULT_PROFILE })
        if (url != cachedUrl || cachedApi == null) {
            cachedApi = build(url)
            cachedUrl = url
        }
        return cachedApi
    }

    /** Drops the cached client, so the next call rebuilds against new settings. */
    @Synchronized
    fun invalidate() {
        cachedApi = null
        cachedUrl = null
    }

    private fun build(baseUrl: String): MangaBrainApi {
        val moshi = Moshi.Builder()
            // Reflective adapter last, so it does not shadow built-ins.
            .addLast(KotlinJsonAdapterFactory())
            .build()

        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BASIC
            // The token must never reach logcat.
            redactHeader("Authorization")
        }

        val client = OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor(tokenProvider))
            .addInterceptor(logging)
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .build()

        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(MoshiConverterFactory.create(moshi).asLenient())
            .build()
            .create(MangaBrainApi::class.java)
    }
}
