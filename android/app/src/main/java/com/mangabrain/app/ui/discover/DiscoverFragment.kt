package com.mangabrain.app.ui.discover

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.TextView
import androidx.core.widget.doAfterTextChanged
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import coil.load
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.mangabrain.app.R
import com.mangabrain.app.data.FilterState
import com.mangabrain.app.data.MixStore
import com.mangabrain.app.data.ServiceLocator
import com.mangabrain.app.ui.GridAdapter
import com.mangabrain.app.ui.RelatedAdapter
import com.mangabrain.app.data.api.MangaBrainApi
import com.mangabrain.app.data.api.MediaOut
import com.mangabrain.app.data.api.RecommendResponse
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Search, browse, and recommendations, in one pane.
 *
 * The web client works the same way and for the same reason: a seed and its
 * results are the same view with the seed card added, so drilling in does
 * not lose the medium, the filters, or the place in the list.
 */
class DiscoverFragment : Fragment(R.layout.fragment_discover) {

    private val mediums = listOf(
        "anime" to "Anime",
        "manga" to "Manga",
        "light_novel" to "Light Novels",
        "one_shot" to "One-Shots"
    )

    private lateinit var grid: GridAdapter
    private lateinit var related: RelatedAdapter
    private var searchJob: Job? = null

    /** What is on screen, so a filter change knows what to re-run. */
    private sealed interface Mode {
        data class Seed(val id: Int) : Mode
        object Mix : Mode
        object ForYou : Mode
        data class Browse(val query: String, val withFilters: Boolean) : Mode
    }

    private var mode: Mode? = null
    private var lastSeedMedia: MediaOut? = null

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        grid = GridAdapter(
            onClick = { media -> loadRecommendations(media.id) },
            onAddToMix = { media ->
                val added = MixStore.add(media)
                status(
                    if (added) getString(R.string.mix_added, media.displayTitle)
                    else getString(R.string.mix_full, MixStore.MAX)
                )
            }
        )
        view.findViewById<RecyclerView>(R.id.resultsGrid).apply {
            layoutManager = GridLayoutManager(requireContext(), 3)
            adapter = grid
        }

        related = RelatedAdapter { id -> loadRecommendations(id) }
        view.findViewById<RecyclerView>(R.id.relatedList).apply {
            layoutManager = LinearLayoutManager(requireContext(), RecyclerView.HORIZONTAL, false)
            adapter = related
        }

        buildMediumChips(view.findViewById(R.id.mediumChips))

        val input: EditText = view.findViewById(R.id.searchInput)
        input.doAfterTextChanged { text ->
            searchJob?.cancel()
            val query = text?.toString()?.trim().orEmpty()
            if (query.isEmpty()) return@doAfterTextChanged
            searchJob = viewLifecycleOwner.lifecycleScope.launch {
                delay(300)
                runSearch(query, withFilters = false)
            }
        }

        view.findViewById<Button>(R.id.browseButton).setOnClickListener {
            searchJob?.cancel()
            runOnScope { runSearch(input.text.toString().trim(), withFilters = true) }
        }
        view.findViewById<Button>(R.id.forYouButton).setOnClickListener { loadForYou() }
        view.findViewById<Button>(R.id.surpriseButton).setOnClickListener { surprise() }
        view.findViewById<Button>(R.id.seedAddToMix).setOnClickListener { addSeedToMix() }
    }

    private fun buildMediumChips(group: ChipGroup) {
        group.removeAllViews()
        for ((key, label) in mediums) {
            val chip = Chip(requireContext()).apply {
                text = label
                isCheckable = true
                isChecked = key == FilterState.medium
                setOnClickListener {
                    FilterState.medium = key
                    // The format vocabulary is per medium, so a format chosen
                    // for anime cannot survive a switch to manga.
                    FilterState.format = ""
                    rerun()
                }
            }
            group.addView(chip)
        }
    }

    // ---------------------------------------------------------------- loads

    private fun api(): MangaBrainApi? {
        val api = ServiceLocator.api(requireContext())
        if (api == null) status(getString(R.string.needs_server))
        return api
    }

    private fun runOnScope(block: suspend () -> Unit) {
        viewLifecycleOwner.lifecycleScope.launch { block() }
    }

    private suspend fun runSearch(query: String, withFilters: Boolean) {
        val api = api() ?: return
        mode = Mode.Browse(query, withFilters)
        showSeed(null)
        status(getString(R.string.searching))
        call {
            val response = if (withFilters) {
                api.search(
                    query.ifBlank { null }, FilterState.searchParams(),
                    FilterState.formats, FilterState.genreIn, FilterState.genreEx,
                    FilterState.tagIn, FilterState.tagEx, FilterState.excludeLists
                )
            } else {
                api.search(query, mapOf("medium" to FilterState.medium, "limit" to "60"))
            }
            grid.submit(response.results.map { GridAdapter.Row(it) })
            count(response.results.size)
            status(if (response.results.isEmpty()) getString(R.string.no_matches) else null)
        }
    }

    fun loadRecommendations(mediaId: Int) {
        val api = api() ?: return
        mode = Mode.Seed(mediaId)
        status(getString(R.string.loading))
        runOnScope {
            call {
                render(
                    api.recommend(
                        mediaId, FilterState.recommendParams(),
                        FilterState.formats, FilterState.genreIn, FilterState.genreEx,
                        FilterState.tagIn, FilterState.tagEx, FilterState.excludeLists
                    )
                )
            }
        }
    }

    fun loadMix() {
        val api = api() ?: return
        if (MixStore.size == 0) return
        mode = Mode.Mix
        status(getString(R.string.loading))
        runOnScope {
            call {
                render(
                    api.recommendMix(
                        MixStore.ids, FilterState.recommendParams(),
                        FilterState.formats, FilterState.genreIn, FilterState.genreEx,
                        FilterState.tagIn, FilterState.tagEx, FilterState.excludeLists
                    )
                )
            }
        }
    }

    private fun loadForYou() {
        val api = api() ?: return
        mode = Mode.ForYou
        status(getString(R.string.loading))
        runOnScope {
            call {
                val params = FilterState.recommendParams().toMutableMap()
                params["medium"] = FilterState.medium
                params["seeds"] = ForYouPrefs.seeds.toString()
                if (FilterState.excludeAnilist && FilterState.anilistUser.isNotBlank()) {
                    params["anilist_user"] = FilterState.anilistUser
                }
                if (FilterState.excludeMal && FilterState.malUser.isNotBlank()) {
                    params["mal_user"] = FilterState.malUser
                }
                render(
                    api.forYou(
                        params, FilterState.formats, FilterState.genreIn, FilterState.genreEx,
                        FilterState.tagIn, FilterState.tagEx, FilterState.excludeLists
                    )
                )
            }
        }
    }

    private fun surprise() {
        val api = api() ?: return
        status(getString(R.string.loading))
        runOnScope {
            call {
                // /random takes no tag filters, so a surprise pick honours
                // every other active filter but not the tag chips.
                val media = api.random(
                    FilterState.filterParams() + mapOf("medium" to FilterState.medium),
                    FilterState.formats, FilterState.genreIn, FilterState.genreEx,
                    FilterState.excludeLists
                )
                loadRecommendations(media.id)
            }
        }
    }

    /** Re-runs whatever is on screen, after a filter or medium change. */
    fun rerun() {
        when (val current = mode) {
            is Mode.Seed -> loadRecommendations(current.id)
            is Mode.Mix -> loadMix()
            is Mode.ForYou -> loadForYou()
            is Mode.Browse -> runOnScope { runSearch(current.query, current.withFilters) }
            null -> Unit
        }
    }

    // --------------------------------------------------------------- render

    private fun render(data: RecommendResponse) {
        showSeed(data)
        grid.submit(data.results.map { GridAdapter.Row(it.media, it.similarity) })
        count(data.results.size)
        status(if (data.results.isEmpty()) getString(R.string.no_recommendations) else null)
    }

    private fun showSeed(data: RecommendResponse?) {
        val view = view ?: return
        val card = view.findViewById<View>(R.id.seedCard)
        val relatedList = view.findViewById<RecyclerView>(R.id.relatedList)
        if (data == null) {
            card.visibility = View.GONE
            relatedList.visibility = View.GONE
            lastSeedMedia = null
            return
        }
        lastSeedMedia = data.seed
        card.visibility = View.VISIBLE
        view.findViewById<TextView>(R.id.seedTitle).text = data.seed.displayTitle
        view.findViewById<TextView>(R.id.seedMeta).text = data.seed.displayMeta
        val cover = data.seed.coverUrl
        val image = view.findViewById<ImageView>(R.id.seedCover)
        if (cover.isNullOrBlank()) image.setImageDrawable(null) else image.load(cover)
        related.submit(data.related)
        relatedList.visibility = if (data.related.isEmpty()) View.GONE else View.VISIBLE
    }

    private fun addSeedToMix() {
        val media = lastSeedMedia ?: return
        val added = MixStore.add(media)
        status(
            if (added) getString(R.string.mix_added, media.displayTitle)
            else getString(R.string.mix_full, MixStore.MAX)
        )
    }

    private fun count(value: Int) {
        val label = view?.findViewById<TextView>(R.id.resultCount) ?: return
        label.visibility = if (value == 0) View.GONE else View.VISIBLE
        label.text = getString(R.string.matches, value)
    }

    private fun status(message: String?) {
        val label = view?.findViewById<TextView>(R.id.statusText) ?: return
        label.text = message ?: ""
        label.visibility = if (message == null) View.GONE else View.VISIBLE
    }

    /** One error taxonomy for every call, so 401 always says the same thing. */
    private suspend fun call(block: suspend () -> Unit) {
        try {
            block()
        } catch (e: retrofit2.HttpException) {
            grid.submit(emptyList())
            count(0)
            status(
                if (e.code() == 401) getString(R.string.error_token)
                else getString(R.string.error_http, e.code())
            )
        } catch (e: Exception) {
            grid.submit(emptyList())
            count(0)
            status(getString(R.string.error_unreachable, e.message ?: ""))
        }
    }
}

/** For-you sampling options, shared with the Mix tab that edits them. */
object ForYouPrefs {
    var seeds: Int = 8
    var useRatings: Boolean = true
}
