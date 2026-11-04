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
        loadDubLanguages()
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
            input.setText(field.get())
            input.addTextChangedListener(SimpleWatcher { field.set(it) })
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

        // The dub filter is anime-only, like the web client's.
        val anime = FilterState.medium == "anime"
        view.findViewById<View>(R.id.dubLabel).visibility = if (anime) View.VISIBLE else View.GONE
        view.findViewById<View>(R.id.dubSpinner).visibility = if (anime) View.VISIBLE else View.GONE
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
     * web client uses, and the same vocabulary, so the two clients cannot
     * offer different genre sets.
     */
    private fun bindGenres(group: ChipGroup) {
        group.removeAllViews()
        for (genre in FilterState.GENRES) {
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
            } catch (e: Exception) {
                // A missing picker is better than a wrong one; English stays
                // reachable through the server's own default behaviour.
            }
        }
    }

    private fun loadTagVocabulary(input: AutoCompleteTextView) {
        val api = ServiceLocator.api(requireContext()) ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val tags = api.tags().tags
                input.setAdapter(
                    ArrayAdapter(requireContext(), android.R.layout.simple_dropdown_item_1line, tags)
                )
            } catch (e: Exception) {
                // Autocomplete is a convenience; typing a tag still works.
            }
        }
    }
}

/** TextWatcher with only the callback that matters. */
private class SimpleWatcher(val onChange: (String) -> Unit) : android.text.TextWatcher {
    override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
    override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
    override fun afterTextChanged(s: android.text.Editable?) = onChange(s?.toString().orEmpty())
}
