package com.mangabrain.app.data.api

import retrofit2.http.GET
import retrofit2.http.Path
import retrofit2.http.Query

/**
 * The two endpoints this first slice needs. Parameter names are taken from
 * /openapi.json, so they match the server's Query(...) names exactly.
 *
 * No default argument values: Retrofit resolves these by reflection, and
 * Kotlin default arguments on interface methods are a needless way to be
 * surprised. Call sites pass everything.
 */
interface MangaBrainApi {

    @GET("search")
    suspend fun search(
        @Query("q") q: String?,
        @Query("medium") medium: String?,
        @Query("limit") limit: Int,
        @Query("adult") adult: Boolean
    ): SearchResponse

    @GET("recommend/{mediaId}")
    suspend fun recommend(
        @Path("mediaId") mediaId: Int,
        @Query("limit") limit: Int,
        @Query("adult") adult: Boolean,
        @Query("exclude_franchise") excludeFranchise: Boolean
    ): RecommendResponse
}
