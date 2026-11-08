package com.mangabrain.app.ui.discover

import android.os.Bundle
import android.view.View
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.core.widget.doAfterTextChanged
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.ConcatAdapter
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.mangabrain.app.R
import com.mangabrain.app.ui.WideScreen
import com.mangabrain.app.data.FilterState
import com.mangabrain.app.data.MixStore
import com.mangabrain.app.data.ServiceLocator
import com.mangabrain.app.data.api.MangaBrainApi
import com.mangabrain.app.data.api.MediaOut
import com.mangabrain.app.data.api.RecommendResponse
import com.mangabrain.app.data.api.httpMessage
import com.mangabrain.app.ui.GridAdapter
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Search, browse, and recommendations, in one pane.
 *
 * The web client works the same way and for the same reason: a seed and its
 * results are the same view with the seed card added, so drilling in does
 * not lose the medium, the filters, or the place in the list.
 *
 * The grid is the only scrolling container here. The seed card, related
 * titles and match count are a full-span header item concatenated in front
 * of it, rather than siblings inside a ScrollView wrapped around the grid -
 * that arrangement stopped the results scrolling at all, and forced every
 * result to be inflated at once, which for a 200-result recommendation
 * meant 200 posters all fetching images.
 */
class DiscoverFragment : Fragment(R.layout.fragment_discover) {

    private lateinit var grid: GridAdapter
    private lateinit var header: DiscoverHeaderAdapter
    private var searchJob: Job? = null

    /**
     * The seeds currently on screen, so a re-rank does not move the list.
     *
     * Same rule as the web client: tapping a title, a Surprise me or a
     * fresh For-you draw is a new page and starts at the top, while moving
     * a slider re-ranks the same seeds and must leave the reader where
     * they were. Without the first half, opening a title from halfway down
     * the grid left the new seed card scrolled off the top of the screen.
     */
    private var shownSeedIds: String? = null

    /** What is on screen, so a filter change knows what to re-run. */
    private sealed interface Mode {
        data class Seed(val id: Int) : Mode
        object Mix : Mode
        object ForYou : Mode
        data class Browse(val query: String, val withFilters: Boolean) : Mode
        data class Describe(val query: String) : Mode
    }

    private var mode: Mode? = null

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        grid = GridAdapter(
            onClick = { media -> loadRecommendations(media.id) },
            onAddToMix = ::addToMix
        )
        header = DiscoverHeaderAdapter(
            onAddToMix = ::addToMix,
            onRelatedClick = { id -> loadRecommendations(id) },
            onRequireGenre = { genre ->
                requireFacet(FilterState.genreState, genre, R.string.requiring_genre)
            },
            onRequireTag = { tag ->
                requireFacet(FilterState.tagState, tag, R.string.requiring_tag)
            }
        )

        // Three on a phone; more on a tablet or an unfolded foldable.
        val columns = WideScreen.gridColumns(requireContext())
        val manager = GridLayoutManager(requireContext(), columns)
        manager.spanSizeLookup = object : GridLayoutManager.SpanSizeLookup() {
            // The header is one row wide; everything after it is one cell.
            override fun getSpanSize(position: Int): Int =
                if (position < header.itemCount) columns else 1
        }
        view.findViewById<RecyclerView>(R.id.resultsGrid).apply {
            layoutManager = manager
            adapter = ConcatAdapter(header, grid)
        }

        buildMediumSpinner(view.findViewById(R.id.mediumSpinner))

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
        val describeInput: EditText = view.findViewById(R.id.describeInput)
        view.findViewById<Button>(R.id.describeButton).setOnClickListener {
            runOnScope { runDescribe(describeInput.text.toString()) }
        }
        // No debounce on this one, unlike the title box: every facet of a
        // description is its own ANN scan, so searching per keystroke
        // would be one vector search per character typed.
        describeInput.setOnEditorActionListener { _, _, _ ->
            runOnScope { runDescribe(describeInput.text.toString()) }
            true
        }

        view.findViewById<Button>(R.id.forYouButton).setOnClickListener { loadForYou() }
        view.findViewById<Button>(R.id.surpriseButton).setOnClickListener { surprise() }
    }

    /**
     * Requires one of the seed's genres or tags and re-ranks, the way
     * clicking a chip on the web seed card does.
     *
     * A Toast rather than the status line: rerun() replaces the status with
     * "Loading..." immediately, so a status message would never be read.
     */
    private fun requireFacet(state: MutableMap<String, String>, name: String, messageRes: Int) {
        state[name] = "inc"
        Toast.makeText(requireContext(), getString(messageRes, name), Toast.LENGTH_SHORT).show()
        rerun()
    }

    private fun addToMix(media: MediaOut) {
        val added = MixStore.add(media)
        status(
            if (added) getString(R.string.mix_added, media.displayTitle)
            else getString(R.string.mix_full, MixStore.MAX)
        )
    }

    /**
     * Settings › Display changed the title language. Titles are read at
     * bind time (MediaOut.displayTitle), so rebinding what is on screen is
     * enough - no request needed.
     */
    fun refreshTitles() {
        if (this::grid.isInitialized) grid.notifyDataSetChanged()
        if (this::header.isInitialized) header.notifyDataSetChanged()
    }

    override fun onResume() {
        super.onResume()
        refreshMedia()
    }

    override fun onHiddenChanged(hidden: Boolean) {
        super.onHiddenChanged(hidden)
        if (!hidden) refreshMedia()
    }

    /**
     * The tabs follow the server's media selection, which the settings
     * screen changes - so they are re-read whenever this screen comes back,
     * including on return from Settings. A failure keeps the tabs as they
     * were: the selection is a convenience, not a requirement.
     */
    private fun refreshMedia() {
        val api = ServiceLocator.api(requireContext()) ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                FilterState.applySettings(api.settings())
            } catch (e: Exception) {
                return@launch
            }
            view?.let { buildMediumSpinner(it.findViewById(R.id.mediumSpinner)) }
        }
    }

    /**
     * The medium dropdown. Rebuilt whenever the selection of media changes
     * (Settings > Media), so it lists exactly the visible ones.
     */
    /** Settings > Media changed the selection; FilterState already has it. */
    fun mediaChanged() {
        view?.let { buildMediumSpinner(it.findViewById(R.id.mediumSpinner)) }
    }

    private fun buildMediumSpinner(spinner: Spinner) {
        val visible = FilterState.visibleMedia()
        // A medium switched off in Settings leaves the dropdown at once, even
        // with its results on screen: keeping it listed while it was in use
        // read as the setting not having worked. Its results go with it.
        if (FilterState.medium !in visible) {
            FilterState.medium = visible.first()
            FilterState.format = ""
            mode = null
            clear()
            status(null)
        }
        val keys = visible
        val labels = keys.map { FilterState.MEDIUM_LABELS[it] ?: it }
        spinner.adapter = ArrayAdapter(requireContext(), R.layout.item_medium_spinner, labels).apply {
            setDropDownViewResource(R.layout.item_medium_dropdown)
        }
        // Listener off while selecting: setSelection reports the item, and
        // that would rerun the search on every rebuild.
        spinner.onItemSelectedListener = null
        spinner.setSelection(keys.indexOf(FilterState.medium).coerceAtLeast(0), false)
        spinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                val key = keys.getOrNull(position) ?: return
                if (key == FilterState.medium) return
                FilterState.medium = key
                // The format vocabulary is per medium, so a format chosen
                // for anime cannot survive a switch to manga.
                FilterState.format = ""
                rerun()
            }

            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
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
            // No seed in browse mode: the header carries only the count.
            header.show(null, emptyList(), response.results.size)
            // A new result set is a new page, whatever produced it.
            shownSeedIds = null
            scrollToTop()
            status(if (response.results.isEmpty()) getString(R.string.no_matches) else null)
        }
    }

    /**
     * Rank the catalog by how well each synopsis matches a sentence.
     *
     * No weights are sent: there is no seed here, so the synopsis / tags /
     * genres sliders have nothing to weigh against each other.
     */
    private suspend fun runDescribe(text: String) {
        val api = api() ?: return
        val query = text.trim()
        if (query.length < MIN_DESCRIPTION) return
        mode = Mode.Describe(query)
        status(getString(R.string.describing))
        call {
            val response = api.describe(
                query, FilterState.describeParams(),
                FilterState.formats, FilterState.genreIn, FilterState.genreEx,
                FilterState.tagIn, FilterState.tagEx, FilterState.excludeLists
            )
            val multi = response.facets.size > 1
            grid.submit(
                response.results.map { item ->
                    GridAdapter.Row(
                        item.media,
                        badge = if (!multi) null else getString(
                            R.string.facet_coverage, item.facets_hit, item.facet_count
                        )
                    )
                }
            )
            header.show(null, emptyList(), response.results.size)
            shownSeedIds = null
            scrollToTop()
            status(
                if (response.results.isEmpty()) getString(R.string.no_description_matches)
                else null
            )
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
                // The Mix tab's switch used to set a field nobody sent, so
                // turning rating weighting off changed nothing.
                params["use_ratings"] = ForYouPrefs.useRatings.toString()
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
            is Mode.Describe -> runOnScope { runDescribe(current.query) }
            null -> Unit
        }
    }

    // --------------------------------------------------------------- render

    private fun render(data: RecommendResponse) {
        header.show(data.seed, data.related, data.results.size)
        grid.submit(data.results.map { GridAdapter.Row(it.media, it.similarity) })
        status(if (data.results.isEmpty()) getString(R.string.no_recommendations) else null)

        val seeds = data.seeds.ifEmpty { listOf(data.seed) }
        val ids = seeds.joinToString(",") { it.id.toString() }
        if (ids != shownSeedIds) {
            shownSeedIds = ids
            scrollToTop()
        }
    }

    /**
     * Jump, not smooth-scroll: the distance can be the whole grid, and an
     * animated trip past sixty posters is slower than the request that
     * produced them.
     */
    private fun scrollToTop() {
        view?.findViewById<RecyclerView>(R.id.resultsGrid)?.scrollToPosition(0)
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
            clear()
            status(
                if (e.code() == 401) getString(R.string.error_token)
                else httpMessage(requireContext(), e)
            )
        } catch (e: Exception) {
            clear()
            status(getString(R.string.error_unreachable, e.message ?: ""))
        }
    }

    private fun clear() {
        grid.submit(emptyList())
        header.show(null, emptyList(), 0)
        shownSeedIds = null
    }
}

/** Matches the server's min_length on q: shorter cannot describe anything. */
private const val MIN_DESCRIPTION = 3

/** For-you sampling options, shared with the Mix tab that edits them. */
object ForYouPrefs {
    var seeds: Int = 8
    var useRatings: Boolean = true
}
