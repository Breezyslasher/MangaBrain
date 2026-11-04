package com.mangabrain.app.data.api

import android.content.Context
import com.mangabrain.app.R
import org.json.JSONArray
import org.json.JSONObject
import retrofit2.HttpException

/**
 * The server's own explanation of a failure, when it sent one.
 *
 * FastAPI puts a human-readable reason in {"detail": ...} on every error it
 * raises, and most of this API's errors are only actionable through that
 * text. A 409 is the clearest case: it means "this seed has no embedding
 * yet, run pipeline.embed" or "seeds span two embedding versions, a
 * re-embed is in progress", and the status code alone distinguishes
 * neither. Showing "Server returned HTTP 409." sends the reader to the
 * source to find out what their phone was told.
 */
fun httpMessage(context: Context, e: HttpException): String {
    val detail = errorDetail(e)
    return if (detail.isNullOrBlank()) {
        context.getString(R.string.error_http, e.code())
    } else {
        context.getString(R.string.error_http_detail, e.code(), detail)
    }
}

/**
 * Parsed rather than regexed out of the body.
 *
 * A regex for "detail":"..." stops at the first quote inside the message and
 * misses FastAPI's 422 shape entirely, where detail is a list of objects
 * rather than a string.
 */
private fun errorDetail(e: HttpException): String? {
    // errorBody() is a one-shot stream: read it once, here, and nowhere else.
    val body = runCatching { e.response()?.errorBody()?.string() }.getOrNull()
    if (body.isNullOrBlank()) return null
    return runCatching {
        when (val detail = JSONObject(body).opt("detail")) {
            null, JSONObject.NULL -> null
            is JSONArray -> (0 until detail.length())
                .mapNotNull { detail.optJSONObject(it)?.optString("msg") }
                .filter { it.isNotBlank() }
                .joinToString("; ")
                .ifBlank { null }
            else -> detail.toString().ifBlank { null }
        }
        // Not JSON at all - a proxy's HTML error page, for instance - leaves
        // the bare status code, which is still better than a parse crash.
    }.getOrNull()
}
