package com.mangabrain.app.data

import android.content.Context

/**
 * Every query knob the app can set, and the parameter maps built from them.
 *
 * This mirrors the web client's state + filterParams() + weightParams()
 * deliberately, including the parts that look odd, because the two clients
 * must ask the server the same questions or they are different products:
 *
 *  - One length control per bound, mapped to the field the medium uses
 *    (episodes for anime, chapters otherwise).
 *  - The dub filter is positive-only and anime-only. A chosen language means
 *    "known to be dubbed in it"; there is no sub-only option, because an
 *    absent cast is not evidence of one.
 *  - Weights are percentages here and fractions on the wire.
 *  - exclude_list vs exclude_lists: /search and /recommend spell it
 *    differently. The caller passes the right list to the right method; the
 *    asymmetry is the server's.
 *
 * Kept in memory for the session and persisted only where the web client
 * persists (the title language is not a filter, so it is not here).
 */
object FilterState {

    var medium: String = "anime"

    // Weights, 0..100 as the sliders show them.
    var wSemantic: Int = 50
    var wTags: Int = 30
    var wGenres: Int = 20
    var wTaste: Int = 0

    var yearMin: String = ""
    var yearMax: String = ""
    var minScore: String = ""
    var minLen: String = ""
    var maxLen: String = ""
    var format: String = ""
    var country: String = ""
    var status: String = ""
    var maxPopularity: String = ""
    var dubLang: String = ""

    var adult: Boolean = false
    var crossMedia: Boolean = false
    var excludeFranchise: Boolean = true
    var keepPlanned: Boolean = false

    /** name -> "inc" | "exc", matching the web client's tri-state chips. */
    val genreState: MutableMap<String, String> = mutableMapOf()
    val tagState: MutableMap<String, String> = mutableMapOf()

    var excludeAnilist: Boolean = false
    var excludeMal: Boolean = false
    var excludeKitsu: Boolean = false
    var excludeYamtrack: Boolean = false

    // Filled from GET /settings so the exclusion parameters can carry them.
    var anilistUser: String = ""
    var malUser: String = ""

    val genreIn: List<String>? get() = genreState.filterValues { it == "inc" }.keys.toList().ifEmpty { null }
    val genreEx: List<String>? get() = genreState.filterValues { it == "exc" }.keys.toList().ifEmpty { null }
    val tagIn: List<String>? get() = tagState.filterValues { it == "inc" }.keys.toList().ifEmpty { null }
    val tagEx: List<String>? get() = tagState.filterValues { it == "exc" }.keys.toList().ifEmpty { null }
    val formats: List<String>? get() = format.takeIf { it.isNotBlank() }?.let { listOf(it) }

    /** Named tracker lists, for whichever of the two spellings the call needs. */
    val excludeLists: List<String>?
        get() = buildList {
            if (excludeKitsu) add("kitsu")
            if (excludeYamtrack) add("yamtrack")
        }.ifEmpty { null }

    /** How many filters differ from their defaults, for the tab badge. */
    val activeCount: Int
        get() {
            var count = 0
            for (value in listOf(yearMin, yearMax, minScore, minLen, maxLen,
                                 format, country, status, maxPopularity, dubLang)) {
                if (value.isNotBlank()) count++
            }
            if (adult) count++
            if (crossMedia) count++
            if (!excludeFranchise) count++
            count += genreState.size + tagState.size
            return count
        }

    /**
     * Scalar query parameters. Blank values are dropped rather than sent
     * empty: FastAPI parses "" for an int parameter as a 422, not as absent.
     */
    fun filterParams(): Map<String, String> {
        val params = mutableMapOf<String, String>()
        fun put(key: String, value: String) {
            if (value.isNotBlank()) params[key] = value
        }
        if (adult) params["adult"] = "true"
        put("year_min", yearMin)
        put("year_max", yearMax)
        put("min_score", minScore)
        put("country", country)
        put("status", status)
        put("max_popularity", maxPopularity)
        if (maxLen.isNotBlank()) {
            params[if (medium == "anime") "max_episodes" else "max_chapters"] = maxLen
        }
        if (minLen.isNotBlank()) {
            params[if (medium == "anime") "min_episodes" else "min_chapters"] = minLen
        }
        if (medium == "anime") put("dub_lang", dubLang)
        if (excludeMal && malUser.isNotBlank()) params["mal_user"] = malUser
        if (excludeAnilist && anilistUser.isNotBlank()) params["anilist_user"] = anilistUser
        if (keepPlanned) params["keep_planned"] = "true"
        return params
    }

    /** Weights are percentages in the UI and fractions on the wire. */
    fun weightParams(): Map<String, String> {
        val params = mutableMapOf(
            "w_semantic" to (wSemantic / 100.0).toString(),
            "w_tags" to (wTags / 100.0).toString(),
            "w_genres" to (wGenres / 100.0).toString()
        )
        if (wTaste > 0) params["w_taste"] = (wTaste / 100.0).toString()
        return params
    }

    /**
     * 60, not the web client's 200. The web asks for 200 and reveals them
     * 60 at a time behind a "show more" button; this grid has no paging, so
     * 200 would mean downloading - and parsing - 140 results nobody scrolls
     * to. Every MediaOut carries its full synopsis, so the difference is
     * most of the payload on a phone connection.
     */
    fun recommendParams(limit: Int = 60): Map<String, String> =
        weightParams() + filterParams() + mapOf(
            "cross_media" to crossMedia.toString(),
            "exclude_franchise" to excludeFranchise.toString(),
            "limit" to limit.toString()
        )

    fun searchParams(limit: Int = 60): Map<String, String> =
        filterParams() + mapOf("medium" to medium, "limit" to limit.toString())

    /** Mirrors the web client's medium -> format vocabulary. */
    fun formatsForMedium(): List<String> = when (medium) {
        "anime" -> listOf("TV", "TV_SHORT", "MOVIE", "OVA", "ONA", "SPECIAL", "MUSIC")
        "manga" -> listOf("MANGA")
        "light_novel" -> listOf("NOVEL")
        "one_shot" -> listOf("ONE_SHOT")
        else -> emptyList()
    }

    /** Account names come from the server's settings, not from local state. */
    fun applySettings(settings: com.mangabrain.app.data.api.AppSettingsOut) {
        anilistUser = settings.anilist_username.orEmpty()
        malUser = settings.mal_username.orEmpty()
    }

    private const val PREFS = "mangabrain_filters"

    /** Only the toggles worth surviving a restart; the rest resets. */
    fun load(context: Context) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        medium = prefs.getString("medium", "anime") ?: "anime"
        wSemantic = prefs.getInt("w_semantic", 50)
        wTags = prefs.getInt("w_tags", 30)
        wGenres = prefs.getInt("w_genres", 20)
        wTaste = prefs.getInt("w_taste", 0)
        adult = prefs.getBoolean("adult", false)
        crossMedia = prefs.getBoolean("cross_media", false)
        excludeFranchise = prefs.getBoolean("exclude_franchise", true)
        keepPlanned = prefs.getBoolean("keep_planned", false)
        excludeAnilist = prefs.getBoolean("ex_anilist", false)
        excludeMal = prefs.getBoolean("ex_mal", false)
        excludeKitsu = prefs.getBoolean("ex_kitsu", false)
        excludeYamtrack = prefs.getBoolean("ex_yamtrack", false)
    }

    fun save(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString("medium", medium)
            .putInt("w_semantic", wSemantic)
            .putInt("w_tags", wTags)
            .putInt("w_genres", wGenres)
            .putInt("w_taste", wTaste)
            .putBoolean("adult", adult)
            .putBoolean("cross_media", crossMedia)
            .putBoolean("exclude_franchise", excludeFranchise)
            .putBoolean("keep_planned", keepPlanned)
            .putBoolean("ex_anilist", excludeAnilist)
            .putBoolean("ex_mal", excludeMal)
            .putBoolean("ex_kitsu", excludeKitsu)
            .putBoolean("ex_yamtrack", excludeYamtrack)
            .apply()
    }

    fun reset() {
        yearMin = ""; yearMax = ""; minScore = ""; minLen = ""; maxLen = ""
        format = ""; country = ""; status = ""; maxPopularity = ""; dubLang = ""
        adult = false; crossMedia = false; excludeFranchise = true
        genreState.clear(); tagState.clear()
    }

    val GENRES = listOf(
        "Action", "Adventure", "Comedy", "Drama", "Ecchi", "Fantasy", "Horror",
        "Mahou Shoujo", "Mecha", "Music", "Mystery", "Psychological", "Romance",
        "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller"
    )
}
