package com.mangabrain.app.ui

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.mangabrain.app.R
import com.mangabrain.app.data.api.RelatedItem

/**
 * Adaptations, sequels and side stories for the current seed.
 *
 * These are relations, not recommendations: the server excludes them from
 * the scored results on purpose, so they are shown as their own row rather
 * than mixed into the grid.
 */
class RelatedAdapter(
    private val onClick: (Int) -> Unit
) : RecyclerView.Adapter<RelatedAdapter.Holder>() {

    private val items = mutableListOf<RelatedItem>()

    fun submit(newItems: List<RelatedItem>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder =
        Holder(LayoutInflater.from(parent.context).inflate(R.layout.item_related, parent, false))

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(holder: Holder, position: Int) = holder.bind(items[position])

    inner class Holder(view: View) : RecyclerView.ViewHolder(view) {
        private val chip: TextView = view as TextView

        fun bind(item: RelatedItem) {
            val relation = item.relation_type.lowercase().replace('_', ' ')
            chip.text = "$relation: ${item.media.displayTitle}"
            chip.setOnClickListener { onClick(item.media.id) }
        }
    }
}
