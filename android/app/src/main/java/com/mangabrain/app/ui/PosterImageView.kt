package com.mangabrain.app.ui

import android.content.Context
import android.util.AttributeSet
import androidx.appcompat.widget.AppCompatImageView

/**
 * An ImageView that is always 2:3, the aspect every cover in the catalog
 * uses (and the same ratio the web client sets with aspect-ratio: 2/3).
 *
 * The grid previously gave the poster a fixed 160dp height against a
 * match_parent width. That is only portrait while the column happens to be
 * narrower than 160dp: on a wide phone or a tablet the three columns are
 * wider than that and every cover came out landscape, cropped through the
 * middle of the artwork.
 *
 * Deriving the height from the measured width instead makes it correct at
 * any column width, which is also why this is a view rather than a
 * ConstraintLayout ratio - the app does not depend on ConstraintLayout and
 * pulling it in for one attribute is not worth it.
 */
class PosterImageView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : AppCompatImageView(context, attrs, defStyleAttr) {

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        super.onMeasure(widthMeasureSpec, heightMeasureSpec)
        val width = measuredWidth
        setMeasuredDimension(width, width * 3 / 2)
    }
}
