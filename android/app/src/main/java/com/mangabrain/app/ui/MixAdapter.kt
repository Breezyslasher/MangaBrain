package com.mangabrain.app.ui

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.mangabrain.app.R
import com.mangabrain.app.data.MixStore

class MixAdapter(
    private val onRemove: (Int) -> Unit
) : RecyclerView.Adapter<MixAdapter.Holder>() {

    private val rows = mutableListOf<MixStore.Entry>()

    fun submit(newRows: List<MixStore.Entry>) {
        rows.clear()
        rows.addAll(newRows)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder =
        Holder(LayoutInflater.from(parent.context).inflate(R.layout.item_mix, parent, false))

    override fun getItemCount(): Int = rows.size

    override fun onBindViewHolder(holder: Holder, position: Int) = holder.bind(rows[position])

    inner class Holder(view: View) : RecyclerView.ViewHolder(view) {
        private val cover: ImageView = view.findViewById(R.id.cover)
        private val title: TextView = view.findViewById(R.id.title)
        private val meta: TextView = view.findViewById(R.id.meta)
        private val remove: Button = view.findViewById(R.id.removeButton)

        fun bind(entry: MixStore.Entry) {
            title.text = entry.title
            meta.text = entry.meta
            val url = entry.cover
            cover.showCover(url)
            remove.setOnClickListener { onRemove(entry.id) }
        }
    }
}
