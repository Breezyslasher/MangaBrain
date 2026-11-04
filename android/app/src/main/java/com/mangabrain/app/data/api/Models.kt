package com.mangabrain.app.data.api

/**
 * Response models, mirroring api/models.py field for field.
 *
 * The property names are the API's snake_case rather than Kotlin camelCase
 * with @Json(name = ...) on every field. That is deliberate: the mapping is
 * then the identity function, so these can be checked against
 * /openapi.json by eye, and a renamed field is a compile error at the call
 * site instead of a silently null property at runtime.
 *
 * Everything the server marks optional is nullable with a default, so a
 * response that omits a field parses rather than throwing - which matters
 * because MediaOut has 26 optional fields out of 28.
 */
data class MediaOut(
    val id: Int,
    val medium: String,
    val id_mal: Int? = null,
    val title: String? = null,
    val title_english: String? = null,
    val title_native: String? = null,
    val description: String? = null,
    val genres: List<String> = emptyList(),
    val tags: List<String> = emptyList(),
    val format: String? = null,
    val episodes: Int? = null,
    val chapters: Int? = null,
    val volumes: Int? = null,
    val country_of_origin: String? = null,
    val start_year: Int? = null,
    val status: String? = null,
    val average_score: Int? = null,
    val cover_image: String? = null,
    val cover_image_large: String? = null,
    val is_adult: Boolean = false,
    val synonyms: List<String> = emptyList(),
    val popularity: Int? = null,
    val favourites: Int? = null,
    val has_english_dub: Boolean? = null,
    val dub_groups: List<String> = emptyList(),
    val dub_languages: List<String> = emptyList(),
    val measured_dub_languages: List<String> = emptyList(),
    val dub_source: String? = null
) {
    /** Same precedence the web client uses for its "English when available". */
    val displayTitle: String
        get() = title_english ?: title ?: title_native ?: "#$id"

    /** Year / format / score, and the dub marker when the server asserts one. */
    val displayMeta: String
        get() {
            val parts = mutableListOf<String>()
            start_year?.let { parts.add(it.toString()) }
            format?.let { parts.add(it.replace('_', ' ')) }
            average_score?.let { parts.add(it.toString()) }
            if (has_english_dub == true) {
                parts.add(if (dub_source == "franchise") "dub (series)" else "dub")
            }
            return parts.joinToString(" / ")
        }

    val coverUrl: String?
        get() = cover_image_large ?: cover_image
}

data class SearchResponse(
    val results: List<MediaOut> = emptyList()
)

data class ScoreComponents(
    val semantic: Double,
    val tags: Double,
    val genres: Double,
    val taste: Double? = null
)

data class RecommendationItem(
    val media: MediaOut,
    val similarity: Double,
    val components: ScoreComponents
)

data class RelatedItem(
    val media: MediaOut,
    val relation_type: String
)

data class RecommendResponse(
    val seed: MediaOut,
    val weights: ScoreComponents,
    val results: List<RecommendationItem> = emptyList(),
    val related: List<RelatedItem> = emptyList(),
    val seeds: List<MediaOut> = emptyList()
)
