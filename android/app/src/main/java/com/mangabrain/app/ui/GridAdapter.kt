package com.mangabrain.app.ui

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import coil.load
import com.mangabrain.app.R
import com.mangabrain.app.data.api.MediaOut

/** The three-column result grid: cover, optional similarity badge, title. */
class GridAdapter(
    private val onClick: (MediaOut) -> Unit,
    private val onAddToMix: (MediaOut) -> Unit
) : RecyclerView.Adapter<GridAdapter.Holder>() {

    data class Row(val media: MediaOut, val similarity: Double? = null)

    private val rows = mutableListOf<Row>()

    fun submit(newRows: List<Row>) {
        rows.clear()
        rows.addAll(newRows)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder =
        Holder(LayoutInflater.from(parent.context).inflate(R.layout.item_grid, parent, false))

    override fun getItemCount(): Int = rows.size

    override fun onBindViewHolder(holder: Holder, position: Int) = holder.bind(rows[position])

    inner class Holder(view: View) : RecyclerView.ViewHolder(view) {
        private val cover: ImageView = view.findViewById(R.id.cover)
        private val title: TextView = view.findViewById(R.id.title)
        private val meta: TextView = view.findViewById(R.id.meta)
        private val badge: TextView = view.findViewById(R.id.badge)
        private val add: Button = view.findViewById(R.id.addToMix)

        fun bind(row: Row) {
            val media = row.media
            title.text = media.displayTitle
            meta.text = media.displayMeta
            if (row.similarity == null) {
                badge.visibility = View.GONE
            } else {
                badge.visibility = View.VISIBLE
                badge.text = itemView.context.getString(
                    R.string.similarity_percent, Math.round(row.similarity).toInt()
                )
            }
            val url = media.coverUrl
            if (url.isNullOrBlank()) cover.setImageDrawable(null) else cover.load(url)
            itemView.setOnClickListener { onClick(media) }
            add.setOnClickListener { onAddToMix(media) }
        }
    }
}
