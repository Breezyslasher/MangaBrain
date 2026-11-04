package com.mangabrain.app.ui.discover

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import coil.load
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.mangabrain.app.R
import com.mangabrain.app.data.api.MediaOut
import com.mangabrain.app.data.api.RelatedItem
import com.mangabrain.app.ui.RelatedAdapter

/**
 * Zero or one item: the seed card, its genres and tags, its synopsis, its
 * related titles, and the match count.
 *
 * Concatenated in front of the result grid rather than placed above it in a
 * ScrollView. That is what lets the grid be the only scrolling container -
 * the header scrolls away with the results, and the grid still recycles, so
 * a 200-result recommendation does not inflate 200 posters at once.
 */
class DiscoverHeaderAdapter(
    private val onAddToMix: (MediaOut) -> Unit,
    private val onRelatedClick: (Int) -> Unit,
    private val onRequireGenre: (String) -> Unit,
    private val onRequireTag: (String) -> Unit
) : RecyclerView.Adapter<DiscoverHeaderAdapter.Holder>() {

    private var seed: MediaOut? = null
    private var related: List<RelatedItem> = emptyList()
    private var count: Int = 0

    /**
     * Whether the synopsis is expanded, held here rather than in the holder.
     * The holder is rebound on every re-rank, so holder state would collapse
     * the text each time a slider moved.
     */
    private var expanded: Boolean = false

    fun show(seed: MediaOut?, related: List<RelatedItem>, count: Int) {
        // A different title starts collapsed; the same title re-ranked keeps
        // whatever the reader chose.
        if (seed?.id != this.seed?.id) expanded = false
        this.seed = seed
        this.related = related
        this.count = count
        notifyDataSetChanged()
    }

    /** Nothing to show means no header row at all, not an empty one. */
    override fun getItemCount(): Int = if (seed != null || count > 0) 1 else 0

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder =
        Holder(
            LayoutInflater.from(parent.context)
                .inflate(R.layout.item_discover_header, parent, false)
        )

    override fun onBindViewHolder(holder: Holder, position: Int) =
        holder.bind(seed, related, count)

    inner class Holder(view: View) : RecyclerView.ViewHolder(view) {
        private val card: View = view.findViewById(R.id.seedCard)
        private val cover: ImageView = view.findViewById(R.id.seedCover)
        private val title: TextView = view.findViewById(R.id.seedTitle)
        private val meta: TextView = view.findViewById(R.id.seedMeta)
        private val addToMix: Button = view.findViewById(R.id.seedAddToMix)
        private val chips: ChipGroup = view.findViewById(R.id.seedChips)
        private val description: TextView = view.findViewById(R.id.seedDescription)
        private val descriptionToggle: Button =
            view.findViewById(R.id.seedDescriptionToggle)
        private val relatedList: RecyclerView = view.findViewById(R.id.relatedList)
        private val countLabel: TextView = view.findViewById(R.id.resultCount)
        private val relatedAdapter = RelatedAdapter(onRelatedClick)

        init {
            relatedList.layoutManager =
                LinearLayoutManager(view.context, RecyclerView.HORIZONTAL, false)
            relatedList.adapter = relatedAdapter
            descriptionToggle.setOnClickListener {
                expanded = !expanded
                applyExpansion()
            }
        }

        fun bind(seed: MediaOut?, related: List<RelatedItem>, count: Int) {
            if (seed == null) {
                card.visibility = View.GONE
            } else {
                card.visibility = View.VISIBLE
                title.text = seed.displayTitle
                meta.text = seed.displayMeta
                val url = seed.coverUrl
                if (url.isNullOrBlank()) cover.setImageDrawable(null) else cover.load(url)
                addToMix.setOnClickListener { onAddToMix(seed) }
                bindChips(seed)
                bindDescription(seed.description)
            }
            relatedAdapter.submit(related)
            relatedList.visibility = if (related.isEmpty()) View.GONE else View.VISIBLE
            countLabel.visibility = if (count == 0) View.GONE else View.VISIBLE
            countLabel.text = itemView.context.getString(R.string.matches, count)
        }

        /**
         * Genres first, then the first ten tags: the same set, the same
         * order and the same cap as the web client's seed card.
         *
         * The tag list the API returns is ordered by AniList rank, so the
         * cap keeps the ten the title is best known for rather than ten
         * arbitrary ones.
         */
        private fun bindChips(seed: MediaOut) {
            chips.removeAllViews()
            for (genre in seed.genres) {
                chips.addView(facetChip(genre, R.color.text) { onRequireGenre(genre) })
            }
            for (tag in seed.tags.take(MAX_TAGS)) {
                chips.addView(facetChip(tag, R.color.muted) { onRequireTag(tag) })
            }
            chips.visibility = if (chips.childCount == 0) View.GONE else View.VISIBLE
        }

        /** Tags are dimmer than genres, the way the web card distinguishes them. */
        private fun facetChip(label: String, textColor: Int, onClick: () -> Unit): Chip =
            Chip(itemView.context).apply {
                text = label
                isCheckable = false
                isClickable = true
                setTextColor(ContextCompat.getColor(context, textColor))
                setOnClickListener { onClick() }
            }

        /**
         * The server already returns description_clean, so this is plain
         * prose: no HTML tags, entities or AniList spoiler blocks to strip
         * on the client.
         */
        private fun bindDescription(text: String?) {
            val synopsis = text?.trim().orEmpty()
            if (synopsis.isEmpty()) {
                // Common for one-shots and obscure entries, where AniList
                // carries no description at all.
                description.visibility = View.GONE
                descriptionToggle.visibility = View.GONE
                return
            }
            description.visibility = View.VISIBLE
            description.text = synopsis
            applyExpansion()
            // Whether the text is actually clipped is only knowable after a
            // layout pass, and a "Read more" on a two-line synopsis that is
            // already fully visible does nothing. Hide it until measured.
            descriptionToggle.visibility = View.GONE
            description.post { updateToggleVisibility() }
        }

        private fun applyExpansion() {
            description.maxLines = if (expanded) Int.MAX_VALUE else COLLAPSED_LINES
            descriptionToggle.setText(
                if (expanded) R.string.read_less else R.string.read_more
            )
            description.post { updateToggleVisibility() }
        }

        private fun updateToggleVisibility() {
            val layout = description.layout ?: return
            val clipped = layout.lineCount > 0 &&
                layout.getEllipsisCount(layout.lineCount - 1) > 0
            // Expanded text is never ellipsized, so without the first term
            // expanding would remove the only way back.
            descriptionToggle.visibility =
                if (expanded || clipped) View.VISIBLE else View.GONE
        }
    }

    private companion object {
        /** Enough to tell what a title is about without burying the results. */
        const val COLLAPSED_LINES = 4

        /** Matches the web client's seed card, which slices tags at ten. */
        const val MAX_TAGS = 10
    }
}
