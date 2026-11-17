package com.mangabrain.app.ui

import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.fragment.app.Fragment
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.mangabrain.app.R
import com.mangabrain.app.data.FilterState
import com.mangabrain.app.data.Prefs
import com.mangabrain.app.ui.discover.DiscoverFragment
import com.mangabrain.app.ui.filters.FiltersFragment
import com.mangabrain.app.ui.mix.MixFragment
import com.mangabrain.app.ui.settings.SettingsFragment

/**
 * Four tabs: Discover, Filters, Mix, Settings.
 *
 * Fragments are created once and shown/hidden rather than replaced, so
 * switching tabs does not discard a result list or a half-filled filter
 * form - which is what replace() would do on every tap.
 */
class MainActivity : AppCompatActivity() {

    private val discover = DiscoverFragment()
    private val filters = FiltersFragment()
    private val mix = MixFragment()
    private val settings = SettingsFragment()
    private var current: Fragment = discover

    private val nav: BottomNavigationView get() = findViewById(R.id.bottomNav)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        padForSystemBars()
        FilterState.load(this)

        if (savedInstanceState == null) {
            supportFragmentManager.beginTransaction().apply {
                add(R.id.fragmentHost, discover, "discover")
                add(R.id.fragmentHost, filters, "filters"); hide(filters)
                add(R.id.fragmentHost, mix, "mix"); hide(mix)
                add(R.id.fragmentHost, settings, "settings"); hide(settings)
            }.commit()
        }

        nav.setOnItemSelectedListener { item ->
            val target = when (item.itemId) {
                R.id.nav_discover -> discover
                R.id.nav_filters -> filters
                R.id.nav_mix -> mix
                else -> settings
            }
            show(target)
            true
        }

        // Nothing works without a server, so say so before the first request
        // fails in four different places: straight to Settings › Server.
        if (!Prefs(this).isConfigured) {
            settings.openServer()
            nav.selectedItemId = R.id.nav_settings
        }
    }

    /**
     * Android 15 and later draw the app edge to edge, under the status bar,
     * the navigation bar and the keyboard, and targeting API 36 removes the
     * opt-out. So the screens pad themselves by those insets:
     *  - the status bar and any camera cutout: the top of the whole screen,
     *    over the screens' own background;
     *  - the navigation bar: the bottom of the tab bar, so its surface runs
     *    on under the gesture handle as it did before;
     *  - the open keyboard: the bottom of the whole screen, so a field being
     *    typed into stays above it, as adjustResize used to do.
     */
    private fun padForSystemBars() {
        val root = findViewById<View>(android.R.id.content)
        root.setBackgroundColor(ContextCompat.getColor(this, R.color.bg))
        val navBottom = nav.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
            )
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime()).bottom
            val typing = ime > bars.bottom
            view.setPadding(bars.left, bars.top, bars.right, if (typing) ime else 0)
            nav.setPadding(
                nav.paddingLeft, nav.paddingTop, nav.paddingRight,
                navBottom + if (typing) 0 else bars.bottom
            )
            WindowInsetsCompat.CONSUMED
        }
    }

    private fun show(target: Fragment) {
        if (target === current) return
        supportFragmentManager.beginTransaction().hide(current).show(target).commit()
        current = target
        if (target is MixFragment) target.refresh()
    }

    /** Filters' Apply, and the mix's Recommend, both land on Discover. */
    fun showDiscover() {
        nav.selectedItemId = R.id.nav_discover
    }

    fun discoverFragment(): DiscoverFragment = discover

    /**
     * The Settings detail pages (an account, Media) replace the tab bar with
     * a back arrow, the way the spec draws them. Only Settings calls this.
     */
    fun setTabBarVisible(visible: Boolean) {
        nav.visibility = if (visible) View.VISIBLE else View.GONE
    }

    override fun onPause() {
        super.onPause()
        FilterState.save(this)
    }
}
