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
    val mal_dub_languages: List<String> = emptyList(),
    val dub_source: String? = null
) {
    /** Same precedence the web client uses for its "English when available". */
    /** English when available, unless Settings › Display asks for Romaji. */
    val displayTitle: String
        get() = if (com.mangabrain.app.data.FilterState.romajiTitles) {
            title ?: title_english ?: title_native ?: "#$id"
        } else {
            title_english ?: title ?: title_native ?: "#$id"
        }

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

/**
 * One description-search result.
 *
 * similarity is a raw query-document cosine, NOT the calibrated percentage
 * a recommendation carries, so the UI shows facet coverage instead of
 * dressing it up as a match score.
 */
data class DescribeItem(
    val media: MediaOut,
    val similarity: Double? = null,
    val facets_hit: Int = 1,
    val facet_count: Int = 1
)

data class DescribeResponse(
    val facets: List<String> = emptyList(),
    val results: List<DescribeItem> = emptyList()
)

data class ScoreComponents(
    val semantic: Double,
    val tags: Double,
    // Nullable: the API sends null when neither side had genres to
    // compare, which is not the same as zero. Declared non-null this
    // would throw on a third of book recommendations, because Open
    // Library has no genre vocabulary.
    val genres: Double? = null,
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

// ---------------------------------------------------------------------
// Accounts, lists and vocabulary. Same rule as above: API names verbatim.
// ---------------------------------------------------------------------

data class AppSettingsOut(
    val anilist_username: String? = null,
    val mal_username: String? = null,
    val kitsu_username: String? = null,
    val yamtrack_url: String? = null,
    val yamtrack_token_set: Boolean? = null,
    val hardcover_token_set: Boolean? = null,
    val tmdb_api_key_set: Boolean? = null,
    val enabled_media: List<String>? = null
)

data class AppSettingsIn(
    val anilist_username: String? = null,
    val mal_username: String? = null,
    val kitsu_username: String? = null,
    val yamtrack_url: String? = null,
    val yamtrack_token: String? = null,
    val hardcover_token: String? = null,
    val tmdb_api_key: String? = null,
    // Null is omitted from the body, so saving accounts leaves the media
    // selection alone. Switching a medium on can start a download.
    val enabled_media: List<String>? = null
)

/** Where a dataset install stands, as the server's worker last reported it. */
data class DatasetInstallOut(
    val state: String,
    val phase: String? = null,
    // Long: the books release is over 3 GB, past what an Int holds.
    val bytes: Long? = null,
    val total: Long? = null,
    val message: String? = null,
    val updated_at: String? = null,
    val next_try: String? = null
)

/** One dataset the server can hold, and the media tabs it fills. */
data class DatasetOut(
    val key: String,
    val label: String,
    val description: String,
    val media: List<String>,
    val download_hint: String? = null,
    val installable: Boolean,
    val installed: Boolean,
    val items: Int,
    val embedded: Int,
    val install: DatasetInstallOut? = null,
    val release_available: Boolean? = null
)

data class DatasetsOut(
    val datasets: List<DatasetOut>
)

data class UserListStatus(
    val list_type: String,
    val entry_count: Int,
    val fetched_at: String
)

data class UserListsStatus(
    val username: String,
    val lists: List<UserListStatus> = emptyList()
)

data class ExclusionStatus(
    val name: String,
    val entry_count: Int,
    val updated_at: String,
    val skipped: Int? = null
)

data class DubLanguageOut(
    val code: String,
    val titles: Int
)

data class DubLanguagesOut(
    val languages: List<DubLanguageOut> = emptyList()
)

data class TagsResponse(
    val tags: List<String> = emptyList()
)
