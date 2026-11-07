package com.mangabrain.app.ui.filters

import android.os.Bundle
import android.view.View
import android.widget.ArrayAdapter
import android.widget.AutoCompleteTextView
import android.widget.Button
import android.widget.EditText
import android.widget.SeekBar
import android.widget.Spinner
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.google.android.material.materialswitch.MaterialSwitch
import com.mangabrain.app.R
import com.mangabrain.app.data.FilterState
import com.mangabrain.app.data.ServiceLocator
import com.mangabrain.app.ui.MainActivity
import kotlinx.coroutines.launch

/**
 * Weights, filters, genres, tags and the three toggles.
 *
 * Nothing here fires a request on change. The web client re-ranks live
 * because a slider drag is cheap on a desktop connection; on a phone that
 * would be a request per pixel, so edits are collected and Apply runs once.
 */
class FiltersFragment : Fragment(R.layout.fragment_filters) {

    private val spinnerValues = mutableMapOf<Int, List<String>>()

    /**
     * The text watcher attached to each field, so a rebind can remove it.
     *
     * Reset rebuilds every control by calling onViewCreated again on the
     * same views. addTextChangedListener adds, so without this each Reset
     * left another watcher on all five fields, every one of them firing on
     * every keystroke for as long as the fragment lived.
     */
    private val textWatchers = mutableMapOf<Int, SimpleWatcher>()

    // Every fragment is added in MainActivity.onCreate, so this binds at app
    // start - on a first run that is BEFORE the server has been configured,
    // and ServiceLocator.api() is null. The old code returned early there and
    // never tried again, so the tag list and the dub picker stayed empty for
    // the life of the process. These flags make the fetch retry whenever the
    // tab is shown, until it actually succeeds.
    private var tagsLoaded = false
    private var genresLoaded = false
    private var dubLoaded = false

    // Genres and tags belong to a medium: TV names its genres "Legal" and
    // "Medical", books "Biography". Both lists are fetched for the medium
    // Discover is on, and again whenever it changes.
    private var vocabularyMedium: String? = null
    private var genres: List<String> = fallbackGenres(FilterState.medium)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        bindWeights(view)
        bindTextFields(view)
        bindSpinners(view)
        bindGenres(view.findViewById(R.id.genreChips))
        bindTags(view)
        bindSwitches(view)

        view.findViewById<Button>(R.id.resetButton).setOnClickListener {
            FilterState.reset()
            // Rebuilding is simpler than reversing every control by hand, and
            // cannot drift from what reset() actually clears.
            onViewCreated(requireView(), null)
        }
        view.findViewById<Button>(R.id.applyButton).setOnClickListener {
            (activity as? MainActivity)?.let { main ->
                main.showDiscover()
                main.discoverFragment().rerun()
            }
        }
        loadRemoteVocabularies()
    }

    /**
     * Fragments here are shown and hidden rather than replaced, so onResume
     * does not fire when a tab becomes visible - onHiddenChanged does. This
     * is the only hook that runs when the user actually opens Filters.
     */
    override fun onHiddenChanged(hidden: Boolean) {
        super.onHiddenChanged(hidden)
        if (!hidden) loadRemoteVocabularies()
    }

    private fun loadRemoteVocabularies() {
        val medium = FilterState.medium
        if (vocabularyMedium != medium) {
            vocabularyMedium = medium
            tagsLoaded = false
            genresLoaded = false
            genres = fallbackGenres(medium)
            view?.findViewById<ChipGroup>(R.id.genreChips)?.let { bindGenres(it) }
        }
        val input = view?.findViewById<AutoCompleteTextView>(R.id.tagInput)
        if (!tagsLoaded && input != null) loadTagVocabulary(input)
        if (!genresLoaded) loadGenres()
        if (!dubLoaded) loadDubLanguages()
    }

    private fun fallbackGenres(medium: String): List<String> =
        if (medium == "anime" || medium == "manga") FilterState.GENRES else emptyList()

    private fun loadGenres() {
        val api = ServiceLocator.api(requireContext()) ?: return
        val medium = FilterState.medium
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val list = api.genres(medium).genres
                // The user may have switched medium while this was in flight.
                if (medium != FilterState.medium) return@launch
                genres = list
                genresLoaded = true
                // A genre this medium does not have would filter every
                // result out, unseen, so it does not carry over.
                FilterState.genreState.keys.retainAll(list.toSet())
                view?.findViewById<ChipGroup>(R.id.genreChips)?.let { bindGenres(it) }
            } catch (e: Exception) {
                // Retried on the next visit; the fallback stays meanwhile.
            }
        }
    }

    private fun bindWeights(view: View) {
        data class Row(val id: Int, val label: Int, val get: () -> Int, val set: (Int) -> Unit)
        val rows = listOf(
            Row(R.id.weightSemantic, R.string.weight_synopsis, { FilterState.wSemantic }, { FilterState.wSemantic = it }),
            Row(R.id.weightTags, R.string.weight_tags, { FilterState.wTags }, { FilterState.wTags = it }),
            Row(R.id.weightGenres, R.string.weight_genres, { FilterState.wGenres }, { FilterState.wGenres = it }),
            Row(R.id.weightTaste, R.string.weight_taste, { FilterState.wTaste }, { FilterState.wTaste = it })
        )
        for (row in rows) {
            val container = view.findViewById<View>(row.id)
            container.findViewById<TextView>(R.id.weightLabel).setText(row.label)
            val value = container.findViewById<TextView>(R.id.weightValue)
            val bar = container.findViewById<SeekBar>(R.id.weightBar)
            bar.progress = row.get()
            value.text = row.get().toString()
            bar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) {
                    value.text = progress.toString()
                    row.set(progress)
                }

                override fun onStartTrackingTouch(seekBar: SeekBar?) = Unit
                override fun onStopTrackingTouch(seekBar: SeekBar?) = Unit
            })
        }
    }

    private fun bindTextFields(view: View) {
        data class Field(val id: Int, val get: () -> String, val set: (String) -> Unit)
        val fields = listOf(
            Field(R.id.yearMin, { FilterState.yearMin }, { FilterState.yearMin = it }),
            Field(R.id.yearMax, { FilterState.yearMax }, { FilterState.yearMax = it }),
            Field(R.id.minScore, { FilterState.minScore }, { FilterState.minScore = it }),
            Field(R.id.minLen, { FilterState.minLen }, { FilterState.minLen = it }),
            Field(R.id.maxLen, { FilterState.maxLen }, { FilterState.maxLen = it })
        )
        for (field in fields) {
            val input = view.findViewById<EditText>(field.id)
            textWatchers.remove(field.id)?.let(input::removeTextChangedListener)
            // Set the text before watching it: setText fires the watcher,
            // and on a rebuild that would write the old value straight back
            // over the one reset() just chose.
            input.setText(field.get())
            val watcher = SimpleWatcher { field.set(it) }
            textWatchers[field.id] = watcher
            input.addTextChangedListener(watcher)
        }
    }

    private fun bindSpinners(view: View) {
        bindSpinner(
            view.findViewById(R.id.formatSpinner),
            listOf("" ) + FilterState.formatsForMedium(),
            listOf(getString(R.string.any)) + FilterState.formatsForMedium().map { it.replace('_', ' ') },
            FilterState.format
        ) { FilterState.format = it }

        bindSpinner(
            view.findViewById(R.id.countrySpinner),
            listOf("", "JP", "KR", "CN", "TW"),
            listOf(getString(R.string.any), "Japan", "Korea", "China", "Taiwan"),
            FilterState.country
        ) { FilterState.country = it }

        bindSpinner(
            view.findViewById(R.id.statusSpinner),
            listOf("", "FINISHED", "RELEASING", "NOT_YET_RELEASED", "HIATUS", "CANCELLED"),
            listOf(getString(R.string.any), "Finished", "Releasing", "Not yet released", "Hiatus", "Cancelled"),
            FilterState.status
        ) { FilterState.status = it }

        bindSpinner(
            view.findViewById(R.id.popularitySpinner),
            listOf("", "200000", "50000", "10000", "2000"),
            listOf(getString(R.string.any), "Under 200k", "Under 50k", "Under 10k", "Under 2k"),
            FilterState.maxPopularity
        ) { FilterState.maxPopularity = it }

        // Visibility from the same answer the query uses, so a control is
        // shown exactly when it would be sent. Offering one that cannot
        // change the result is the worse failure of the two: on the Books
        // tab, Origin and Status do not merely get ignored, they match no
        // book at all, so the grid empties with nothing to explain why.
        fun rows(control: String, vararg ids: Int) {
            val visible = if (FilterState.supports(control)) View.VISIBLE else View.GONE
            for (id in ids) view.findViewById<View>(id)?.visibility = visible
        }
        rows("dub_lang", R.id.dubLabel, R.id.dubSpinner)
        rows("min_score", R.id.minScore)
        rows("country", R.id.originLabel, R.id.countrySpinner)
        rows("status", R.id.statusLabel, R.id.statusSpinner)
        rows("max_popularity", R.id.popularityLabel, R.id.popularitySpinner)
        rows("length", R.id.lengthRow)
        // Format has no table entry: the vocabulary itself is the answer, and
        // for books it is empty, leaving a spinner whose only choice is "Any".
        val hasFormats = FilterState.formatsForMedium().isNotEmpty()
        for (id in listOf(R.id.formatLabel, R.id.formatSpinner)) {
            view.findViewById<View>(id)?.visibility = if (hasFormats) View.VISIBLE else View.GONE
        }
    }

    private fun bindSpinner(
        spinner: Spinner,
        values: List<String>,
        labels: List<String>,
        selected: String,
        onPick: (String) -> Unit
    ) {
        spinner.adapter = ArrayAdapter(
            requireContext(), android.R.layout.simple_spinner_dropdown_item, labels
        )
        spinnerValues[spinner.id] = values
        val index = values.indexOf(selected).coerceAtLeast(0)
        spinner.setSelection(index)
        spinner.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
            override fun onItemSelected(
                parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long
            ) {
                onPick(values.getOrElse(position) { "" })
            }

            override fun onNothingSelected(parent: android.widget.AdapterView<*>?) = Unit
        }
    }

    /**
     * Tri-state genre chips: neutral, require, exclude. The same cycle the
     * web client uses, and the same per-medium vocabulary from the server,
     * so the two clients cannot offer different genre sets.
     */
    private fun bindGenres(group: ChipGroup) {
        group.removeAllViews()
        for (genre in genres) {
            val chip = Chip(requireContext()).apply {
                text = genre
                isCheckable = false
                setOnClickListener {
                    val next = when (FilterState.genreState[genre]) {
                        null -> "inc"
                        "inc" -> "exc"
                        else -> null
                    }
                    if (next == null) {
                        FilterState.genreState.remove(genre)
                    } else {
                        FilterState.genreState[genre] = next
                    }
                    paint(this, next)
                }
            }
            paint(chip, FilterState.genreState[genre])
            group.addView(chip)
        }
    }

    private fun paint(chip: Chip, state: String?) {
        val colors = when (state) {
            "inc" -> R.color.ok_bg to R.color.accent_2
            "exc" -> R.color.bad_bg to R.color.bad
            else -> R.color.panel to R.color.muted
        }
        chip.setChipBackgroundColorResource(colors.first)
        chip.setTextColor(ContextCompat.getColor(requireContext(), colors.second))
    }

    private fun bindTags(view: View) {
        val input = view.findViewById<AutoCompleteTextView>(R.id.tagInput)
        val chips = view.findViewById<ChipGroup>(R.id.tagChips)
        renderTagChips(chips)
        view.findViewById<Button>(R.id.tagRequire).setOnClickListener {
            addTag(input, chips, "inc")
        }
        view.findViewById<Button>(R.id.tagExclude).setOnClickListener {
            addTag(input, chips, "exc")
        }
        loadTagVocabulary(input)
    }

    private fun addTag(input: AutoCompleteTextView, chips: ChipGroup, state: String) {
        val tag = input.text.toString().trim()
        if (tag.isEmpty()) return
        FilterState.tagState[tag] = state
        input.setText("")
        renderTagChips(chips)
    }

    private fun renderTagChips(group: ChipGroup) {
        group.removeAllViews()
        for ((tag, state) in FilterState.tagState) {
            val chip = Chip(requireContext()).apply {
                text = tag
                isCloseIconVisible = true
                setOnCloseIconClickListener {
                    FilterState.tagState.remove(tag)
                    renderTagChips(group)
                }
            }
            paint(chip, state)
            group.addView(chip)
        }
    }

    private fun bindSwitches(view: View) {
        data class Toggle(val id: Int, val get: () -> Boolean, val set: (Boolean) -> Unit)
        val toggles = listOf(
            Toggle(R.id.excludeFranchiseSwitch, { FilterState.excludeFranchise }, { FilterState.excludeFranchise = it }),
            Toggle(R.id.crossMediaSwitch, { FilterState.crossMedia }, { FilterState.crossMedia = it }),
            Toggle(R.id.adultSwitch, { FilterState.adult }, { FilterState.adult = it })
        )
        for (toggle in toggles) {
            val control = view.findViewById<MaterialSwitch>(toggle.id)
            control.isChecked = toggle.get()
            control.setOnCheckedChangeListener { _, checked -> toggle.set(checked) }
        }
    }

    /**
     * The dub picker is filled from the server, never hardcoded: the catalog
     * holds far more dub languages than any list written by hand, and a
     * hardcoded set already went stale once on the web client.
     */
    private fun loadDubLanguages() {
        val api = ServiceLocator.api(requireContext()) ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val response = api.dubLanguages(10)
                val codes = listOf("") + response.languages.map { it.code }
                val labels = listOf(getString(R.string.any)) +
                    response.languages.map { "${it.code.uppercase()} (${it.titles})" }
                val spinner = view?.findViewById<Spinner>(R.id.dubSpinner) ?: return@launch
                bindSpinner(spinner, codes, labels, FilterState.dubLang) { FilterState.dubLang = it }
                dubLoaded = true
            } catch (e: Exception) {
                // Left unloaded on purpose: loadRemoteVocabularies retries on
                // the next visit rather than leaving a one-option picker for
                // the rest of the session.
            }
        }
    }

    private fun loadTagVocabulary(input: AutoCompleteTextView) {
        val api = ServiceLocator.api(requireContext())
        if (api == null) {
            tagStatus(getString(R.string.tags_need_server))
            return
        }
        val medium = FilterState.medium
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val tags = api.tags(medium).tags
                if (medium != FilterState.medium) return@launch
                val adapter = TagSuggestionAdapter(requireContext())
                adapter.setVocabulary(tags)
                input.setAdapter(adapter)
                tagsLoaded = tags.isNotEmpty()
                // Silent on success: a permanent "412 tags loaded" line is
                // noise. Only the broken case needs saying.
                tagStatus(if (tags.isEmpty()) getString(R.string.tags_empty) else null)
            } catch (e: Exception) {
                // Previously swallowed, which is why an unreachable server
                // looked exactly like autocomplete simply not being wired.
                tagStatus(getString(R.string.tags_failed, e.message ?: ""))
            }
        }
    }

    private fun tagStatus(message: String?) {
        val label = view?.findViewById<TextView>(R.id.tagStatus) ?: return
        label.text = message ?: ""
        label.visibility = if (message == null) View.GONE else View.VISIBLE
    }
}

/** TextWatcher with only the callback that matters. */
private class SimpleWatcher(val onChange: (String) -> Unit) : android.text.TextWatcher {
    override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
    override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
    override fun afterTextChanged(s: android.text.Editable?) = onChange(s?.toString().orEmpty())
}
