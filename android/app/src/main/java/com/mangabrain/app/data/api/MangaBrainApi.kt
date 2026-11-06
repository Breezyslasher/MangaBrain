package com.mangabrain.app.data.api

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.PUT
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import retrofit2.http.QueryMap

/**
 * Parameter names come from /openapi.json and must match the server's
 * Query(...) names exactly: FastAPI silently ignores an unknown query
 * parameter, so a typo here is a filter that quietly does not filter.
 * tests/test_android_models.py checks them on every run.
 *
 * Scalars travel in a @QueryMap built by FilterState, because the filter
 * surface is 25 parameters wide and listing each one here would be 25 more
 * chances to misspell one. Repeated parameters (format, genre_in, ...)
 * cannot live in a Map and stay explicit.
 *
 * Note /search takes `exclude_lists` while /recommend takes `exclude_list`.
 * That asymmetry is the server's; it is mirrored rather than corrected here.
 */
interface MangaBrainApi {

    @GET("search")
    suspend fun search(
        @Query("q") q: String?,
        @QueryMap params: Map<String, String>,
        @Query("format") format: List<String>? = null,
        @Query("genre_in") genreIn: List<String>? = null,
        @Query("genre_ex") genreEx: List<String>? = null,
        @Query("tag_in") tagIn: List<String>? = null,
        @Query("tag_ex") tagEx: List<String>? = null,
        @Query("exclude_lists") excludeLists: List<String>? = null
    ): SearchResponse

    /**
     * Free-text description search. Takes the recommend spelling of the
     * exclusion parameter (exclude_list), not the /search one.
     */
    @GET("describe")
    suspend fun describe(
        @Query("q") q: String,
        @QueryMap params: Map<String, String>,
        @Query("format") format: List<String>? = null,
        @Query("genre_in") genreIn: List<String>? = null,
        @Query("genre_ex") genreEx: List<String>? = null,
        @Query("tag_in") tagIn: List<String>? = null,
        @Query("tag_ex") tagEx: List<String>? = null,
        @Query("exclude_list") excludeList: List<String>? = null
    ): DescribeResponse

    @GET("recommend/{mediaId}")
    suspend fun recommend(
        @Path("mediaId") mediaId: Int,
        @QueryMap params: Map<String, String>,
        @Query("format") format: List<String>? = null,
        @Query("genre_in") genreIn: List<String>? = null,
        @Query("genre_ex") genreEx: List<String>? = null,
        @Query("tag_in") tagIn: List<String>? = null,
        @Query("tag_ex") tagEx: List<String>? = null,
        @Query("exclude_list") excludeList: List<String>? = null
    ): RecommendResponse

    /** Multi-seed: the mix. */
    @GET("recommend")
    suspend fun recommendMix(
        @Query("ids") ids: List<Int>,
        @QueryMap params: Map<String, String>,
        @Query("format") format: List<String>? = null,
        @Query("genre_in") genreIn: List<String>? = null,
        @Query("genre_ex") genreEx: List<String>? = null,
        @Query("tag_in") tagIn: List<String>? = null,
        @Query("tag_ex") tagEx: List<String>? = null,
        @Query("exclude_list") excludeList: List<String>? = null
    ): RecommendResponse

    @GET("foryou")
    suspend fun forYou(
        @QueryMap params: Map<String, String>,
        @Query("format") format: List<String>? = null,
        @Query("genre_in") genreIn: List<String>? = null,
        @Query("genre_ex") genreEx: List<String>? = null,
        @Query("tag_in") tagIn: List<String>? = null,
        @Query("tag_ex") tagEx: List<String>? = null,
        @Query("exclude_list") excludeList: List<String>? = null
    ): RecommendResponse

    /**
     * Surprise me. Note the parameter set is NOT the same as the others:
     * /random accepts genres but has no tag filters at all, and spells the
     * exclusion list exclude_list. Passing tag_in here would be silently
     * dropped by FastAPI rather than rejected, so the tag filters are
     * deliberately absent and the caller is responsible for knowing that a
     * random pick ignores them.
     */
    @GET("random")
    suspend fun random(
        @QueryMap params: Map<String, String>,
        @Query("format") format: List<String>? = null,
        @Query("genre_in") genreIn: List<String>? = null,
        @Query("genre_ex") genreEx: List<String>? = null,
        @Query("exclude_list") excludeList: List<String>? = null
    ): MediaOut

    @GET("tags")
    suspend fun tags(): TagsResponse

    @GET("dub/languages")
    suspend fun dubLanguages(@Query("min_titles") minTitles: Int): DubLanguagesOut

    @GET("settings")
    suspend fun settings(): AppSettingsOut

    @PUT("settings")
    suspend fun saveSettings(@Body body: AppSettingsIn): AppSettingsOut

    @GET("datasets")
    suspend fun datasets(): DatasetsOut

    @POST("datasets/{key}/install")
    suspend fun installDataset(@Path("key") key: String): DatasetOut

    @POST("anilist/{username}/refresh")
    suspend fun refreshAnilist(@Path("username") username: String): UserListsStatus

    @POST("mal/{username}/refresh")
    suspend fun refreshMal(@Path("username") username: String): UserListsStatus

    @POST("kitsu/refresh")
    suspend fun refreshKitsu(): ExclusionStatus

    @POST("yamtrack/refresh")
    suspend fun refreshYamtrack(): ExclusionStatus
}
