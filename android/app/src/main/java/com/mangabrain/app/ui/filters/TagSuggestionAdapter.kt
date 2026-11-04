package com.mangabrain.app.ui.filters

import android.content.Context
import android.widget.ArrayAdapter
import android.widget.Filter

/**
 * Tag autocomplete, matching the web client's behaviour rather than
 * Android's default.
 *
 * ArrayAdapter's built-in filter matches only the START of the string or of
 * a word within it, so typing "rav" finds nothing while "Time Travel" sits
 * in the vocabulary. The web client ranks prefix matches first and then
 * falls back to substring matches, capped at twelve, which is what makes
 * partial recall of a tag name work - and tags like "Time Skip" or
 * "Seinen" are exactly the kind of thing people half-remember.
 *
 * The cap matters on a phone for a second reason: the dropdown overlays the
 * form, and an unbounded list of several hundred tags would cover the whole
 * screen.
 */
class TagSuggestionAdapter(
    context: Context
) : ArrayAdapter<String>(context, android.R.layout.simple_dropdown_item_1line) {

    private val vocabulary = mutableListOf<String>()
    private val matches = mutableListOf<String>()

    fun setVocabulary(tags: List<String>) {
        vocabulary.clear()
        vocabulary.addAll(tags)
    }

    override fun getCount(): Int = matches.size

    override fun getItem(position: Int): String = matches[position]

    override fun getFilter(): Filter = object : Filter() {

        override fun performFiltering(constraint: CharSequence?): FilterResults {
            val query = constraint?.toString()?.trim()?.lowercase().orEmpty()
            val results = FilterResults()
            if (query.isEmpty()) {
                results.values = emptyList<String>()
                results.count = 0
                return results
            }
            val starts = mutableListOf<String>()
            val contains = mutableListOf<String>()
            for (name in vocabulary) {
                val lower = name.lowercase()
                when {
                    lower.startsWith(query) -> starts.add(name)
                    lower.contains(query) -> contains.add(name)
                }
                // Enough prefix matches to fill the list: nothing further
                // can outrank them, so stop scanning.
                if (starts.size >= MAX_SUGGESTIONS) break
            }
            val ranked = (starts + contains).take(MAX_SUGGESTIONS)
            results.values = ranked
            results.count = ranked.size
            return results
        }

        @Suppress("UNCHECKED_CAST")
        override fun publishResults(constraint: CharSequence?, results: FilterResults?) {
            matches.clear()
            (results?.values as? List<String>)?.let { matches.addAll(it) }
            if (matches.isEmpty()) notifyDataSetInvalidated() else notifyDataSetChanged()
        }

        /** What lands in the field when a suggestion is tapped. */
        override fun convertResultToString(resultValue: Any?): CharSequence =
            resultValue as? String ?: ""
    }

    private companion object {
        const val MAX_SUGGESTIONS = 12
    }
}
