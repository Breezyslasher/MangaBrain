package com.mangabrain.app.ui.discover

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import coil.load
import com.mangabrain.app.R
import com.mangabrain.app.data.api.MediaOut
import com.mangabrain.app.data.api.RelatedItem
import com.mangabrain.app.ui.RelatedAdapter

/**
 * Zero or one item: the seed card, its related titles, and the match count.
 *
 * Concatenated in front of the result grid rather than placed above it in a
 * ScrollView. That is what lets the grid be the only scrolling container -
 * the header scrolls away with the results, and the grid still recycles, so
 * a 200-result recommendation does not inflate 200 posters at once.
 */
class DiscoverHeaderAdapter(
    private val onAddToMix: (MediaOut) -> Unit,
    private val onRelatedClick: (Int) -> Unit
) : RecyclerView.Adapter<DiscoverHeaderAdapter.Holder>() {

    private var seed: MediaOut? = null
    private var related: List<RelatedItem> = emptyList()
    private var count: Int = 0

    fun show(seed: MediaOut?, related: List<RelatedItem>, count: Int) {
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
        private val relatedList: RecyclerView = view.findViewById(R.id.relatedList)
        private val countLabel: TextView = view.findViewById(R.id.resultCount)
        private val relatedAdapter = RelatedAdapter(onRelatedClick)

        init {
            relatedList.layoutManager =
                LinearLayoutManager(view.context, RecyclerView.HORIZONTAL, false)
            relatedList.adapter = relatedAdapter
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
            }
            relatedAdapter.submit(related)
            relatedList.visibility = if (related.isEmpty()) View.GONE else View.VISIBLE
            countLabel.visibility = if (count == 0) View.GONE else View.VISIBLE
            countLabel.text = itemView.context.getString(R.string.matches, count)
        }
    }
}
