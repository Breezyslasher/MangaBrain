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

    /**
     * Medium key to tab label, in tab order. One list for the Discover tabs
     * and the settings screen, matching the web client's tab buttons.
     */
    val MEDIUM_LABELS: Map<String, String> = linkedMapOf(
        "anime" to "Anime",
        "manga" to "Manga",
        "light_novel" to "Light Novels",
        "one_shot" to "One-Shots",
        "book" to "Books"
    )

    /**
     * Romaji titles instead of English ones (Settings › Display). A saved
     * preference rather than a filter, which is why it is set in Settings
     * and not on the Filters sheet; MediaOut.displayTitle reads it.
     */
    var romajiTitles: Boolean = false

    /** The server's selection (GET /settings). Empty until it answers. */
    var enabledMedia: List<String> = emptyList()

    /** The tabs to show: the selection, or every tab before it is known. */
    fun visibleMedia(): List<String> =
        MEDIUM_LABELS.keys.filter { enabledMedia.isEmpty() || it in enabledMedia }
            .ifEmpty { MEDIUM_LABELS.keys.toList() }

    /**
     * Controls a medium has no data behind, by the key filterParams() uses.
     *
     * Suppressing the value matters more than hiding the control, because
     * this is a singleton that outlives the tab: a score typed on the Anime
     * tab was still in minScore on the Books tab and still went into the
     * query. Three of these exclude every book at the SQL level rather than
     * being ignored, since a NULL country never equals 'JP'.
     *
     * min_score is here for a different reason from the rest. A book can
     * have no score and still be a good book, so the API lets unscored
     * titles pass the floor - which makes the control a no-op on this tab
     * rather than a filter, and a no-op control is worse than an absent one.
     *
     * "length" covers both bounds, which share a row and a source column.
     */
    private val UNSUPPORTED: Map<String, Set<String>> = mapOf(
        "book" to setOf("min_score", "country", "status", "max_popularity", "length")
    )

    fun supports(control: String): Boolean {
        // The dub filter is anime-only and always has been. Expressing that
        // here rather than at each use keeps one answer to "is this control
        // live?", which the badge and the query both need to agree on.
        if (control == "dub_lang") return medium == "anime"
        return control !in (UNSUPPORTED[medium] ?: emptySet())
    }

    const val DEFAULT_W_SEMANTIC = 50
    const val DEFAULT_W_TAGS = 30
    const val DEFAULT_W_GENRES = 20
    const val DEFAULT_W_TASTE = 0

    // Weights, 0..100 as the sliders show them. The defaults are the web
    // client's slider values, which are in turn the API's own defaults
    // (0.5 / 0.3 / 0.2), so all three agree on what an unweighted request
    // means. Declared once because load() and reset() both need them, and
    // the point of a default is that there is only one.
    var wSemantic: Int = DEFAULT_W_SEMANTIC
    var wTags: Int = DEFAULT_W_TAGS
    var wGenres: Int = DEFAULT_W_GENRES
    var wTaste: Int = DEFAULT_W_TASTE

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
            // Only what is actually sent. A suppressed control still holds
            // the value typed on another tab, and counting it made the badge
            // claim filters the query did not carry.
            val counted = listOf(
                yearMin to "year", yearMax to "year", format to "format",
                minScore to "min_score", country to "country", status to "status",
                maxPopularity to "max_popularity",
                minLen to "length", maxLen to "length",
                dubLang to "dub_lang"
            )
            for ((value, control) in counted) {
                if (value.isNotBlank() && supports(control)) count++
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
        if (supports("min_score")) put("min_score", minScore)
        if (supports("country")) put("country", country)
        if (supports("status")) put("status", status)
        if (supports("max_popularity")) put("max_popularity", maxPopularity)
        if (supports("length") && maxLen.isNotBlank()) {
            params[if (medium == "anime") "max_episodes" else "max_chapters"] = maxLen
        }
        if (supports("length") && minLen.isNotBlank()) {
            params[if (medium == "anime") "min_episodes" else "min_chapters"] = minLen
        }
        if (supports("dub_lang")) put("dub_lang", dubLang)
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

    /**
     * Filters plus the medium, with no weights: a description has no seed
     * to weigh components against, so the sliders mean nothing here.
     */
    fun describeParams(limit: Int = 60): Map<String, String> =
        filterParams() + mapOf("medium" to medium, "limit" to limit.toString())

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
        enabledMedia = settings.enabled_media.orEmpty()
    }


    private const val PREFS = "mangabrain_filters"

    /** Only the toggles worth surviving a restart; the rest resets. */
    fun load(context: Context) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        medium = prefs.getString("medium", "anime") ?: "anime"
        wSemantic = prefs.getInt("w_semantic", DEFAULT_W_SEMANTIC)
        wTags = prefs.getInt("w_tags", DEFAULT_W_TAGS)
        wGenres = prefs.getInt("w_genres", DEFAULT_W_GENRES)
        wTaste = prefs.getInt("w_taste", DEFAULT_W_TASTE)
        adult = prefs.getBoolean("adult", false)
        crossMedia = prefs.getBoolean("cross_media", false)
        excludeFranchise = prefs.getBoolean("exclude_franchise", true)
        keepPlanned = prefs.getBoolean("keep_planned", false)
        excludeAnilist = prefs.getBoolean("ex_anilist", false)
        excludeMal = prefs.getBoolean("ex_mal", false)
        excludeKitsu = prefs.getBoolean("ex_kitsu", false)
        excludeYamtrack = prefs.getBoolean("ex_yamtrack", false)
        romajiTitles = prefs.getBoolean("romaji_titles", false)
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
            .putBoolean("romaji_titles", romajiTitles)
            .apply()
    }

    /**
     * Reset restores the weights too.
     *
     * It used to clear only the filters, which left a reader who had dragged
     * Synopsis to 0 with no way back to 50/30/20 except by eye - the one
     * state the button exists to escape.
     */
    fun reset() {
        wSemantic = DEFAULT_W_SEMANTIC
        wTags = DEFAULT_W_TAGS
        wGenres = DEFAULT_W_GENRES
        wTaste = DEFAULT_W_TASTE
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
