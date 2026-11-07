package com.mangabrain.app.ui

import android.widget.ImageView
import coil.dispose
import coil.load

/**
 * Shows a title's cover in a recycled view, or nothing when it has none.
 *
 * A recycled card may still have the previous title's cover loading into
 * it. A new load() cancels that request, but clearing the image for a
 * title without a cover did not, so the old request finished afterwards
 * and put another book's cover under this title's name. dispose() cancels
 * it first.
 */
fun ImageView.showCover(url: String?) {
    if (url.isNullOrBlank()) {
        dispose()
        setImageDrawable(null)
    } else {
        load(url)
    }
}
