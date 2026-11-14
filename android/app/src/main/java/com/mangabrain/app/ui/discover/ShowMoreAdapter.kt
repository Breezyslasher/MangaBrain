package com.mangabrain.app.ui.discover

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import androidx.recyclerview.widget.RecyclerView
import com.mangabrain.app.R

/**
 * The "Show more" row after the result grid. Zero items when there is
 * nothing more to fetch, one while there may be; a full-span footer in the
 * same ConcatAdapter as the header and the grid.
 */
class ShowMoreAdapter : RecyclerView.Adapter<ShowMoreAdapter.Holder>() {

    private var onMore: (() -> Unit)? = null
    private var loading = false

    /** Shows the button, which calls [action] once per press. */
    fun offer(action: () -> Unit) {
        val was = onMore != null
        onMore = action
        loading = false
        if (was) notifyItemChanged(0) else notifyItemInserted(0)
    }

    /** Hides the button: the list is complete, or a new one replaced it. */
    fun withdraw() {
        if (onMore == null) return
        onMore = null
        loading = false
        notifyItemRemoved(0)
    }

    /** Puts the button back after a failed fetch, so it can be pressed again. */
    fun failed() {
        if (onMore == null) return
        loading = false
        notifyItemChanged(0)
    }

    override fun getItemCount(): Int = if (onMore == null) 0 else 1

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder =
        Holder(LayoutInflater.from(parent.context).inflate(R.layout.item_show_more, parent, false))

    override fun onBindViewHolder(holder: Holder, position: Int) = holder.bind()

    inner class Holder(view: View) : RecyclerView.ViewHolder(view) {
        private val button: Button = view.findViewById(R.id.showMoreButton)

        fun bind() {
            button.isEnabled = !loading
            button.setText(if (loading) R.string.loading else R.string.show_more)
            button.setOnClickListener {
                val action = onMore ?: return@setOnClickListener
                loading = true
                bind()
                action()
            }
        }
    }
}
