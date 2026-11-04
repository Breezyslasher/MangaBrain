package com.mangabrain.app.data

import com.mangabrain.app.data.api.MediaOut

/**
 * Seed titles for a multi-seed recommendation, held for the session.
 *
 * Capped at five, matching the web client: /recommend averages the seed
 * embeddings, and past a handful the average stops resembling any of them.
 */
object MixStore {

    const val MAX = 5

    data class Entry(val id: Int, val title: String, val cover: String?, val meta: String)

    private val entries = mutableListOf<Entry>()

    val items: List<Entry> get() = entries.toList()
    val size: Int get() = entries.size
    val ids: List<Int> get() = entries.map { it.id }

    /** Returns false when the title is already in the mix or the mix is full. */
    fun add(media: MediaOut): Boolean {
        if (entries.any { it.id == media.id } || entries.size >= MAX) return false
        entries.add(Entry(media.id, media.displayTitle, media.coverUrl, media.displayMeta))
        return true
    }

    fun remove(id: Int) {
        entries.removeAll { it.id == id }
    }
}
