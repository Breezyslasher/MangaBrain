package com.mangabrain.app.ui

import android.content.Context
import android.view.View
import kotlin.math.max

/**
 * Tablets and unfolded foldables. The app was laid out for a phone: three
 * cover columns, and pages as wide as the screen. On a 1280dp tablet that
 * made each cover over 400dp wide, so only three titles fitted on screen,
 * and settings rows ran the full width of the display.
 *
 * The activity is recreated when the screen size changes (folding,
 * unfolding, rotating), so reading the width when a view is created is
 * enough.
 */
object WideScreen {
    /** A cover column is about this wide, whatever the screen. */
    private const val COLUMN_DP = 150

    /** Pages of text and controls stop growing past this width. */
    const val PAGE_MAX_DP = 720

    /** Three columns on a phone, more as the screen widens. */
    fun gridColumns(context: Context): Int =
        max(3, context.resources.configuration.screenWidthDp / COLUMN_DP)
}

/**
 * Keeps this view's content at most [maxDp] wide and centred, by widening
 * its side padding. Its own padding stays the minimum.
 */
fun View.centerContent(maxDp: Int = WideScreen.PAGE_MAX_DP) {
    val baseStart = paddingStart
    val baseEnd = paddingEnd
    val maxPx = (maxDp * resources.displayMetrics.density).toInt()
    addOnLayoutChangeListener { view, left, _, right, _, oldLeft, _, oldRight, _ ->
        val width = right - left
        if (width == oldRight - oldLeft && oldRight != 0) return@addOnLayoutChangeListener
        val extra = max(0, (width - maxPx) / 2)
        val start = max(baseStart, extra)
        val end = max(baseEnd, extra)
        if (start != view.paddingStart || end != view.paddingEnd) {
            view.post { view.setPaddingRelative(start, view.paddingTop, end, view.paddingBottom) }
        }
    }
}
