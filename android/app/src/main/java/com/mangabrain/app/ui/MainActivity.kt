package com.mangabrain.app.ui

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.Fragment
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.mangabrain.app.R
import com.mangabrain.app.data.FilterState
import com.mangabrain.app.data.Prefs
import com.mangabrain.app.ui.discover.DiscoverFragment
import com.mangabrain.app.ui.filters.FiltersFragment
import com.mangabrain.app.ui.lists.ListsFragment
import com.mangabrain.app.ui.mix.MixFragment

/**
 * Four tabs: Discover, Filters, Mix, Lists.
 *
 * Fragments are created once and shown/hidden rather than replaced, so
 * switching tabs does not discard a result list or a half-filled filter
 * form - which is what replace() would do on every tap.
 */
class MainActivity : AppCompatActivity() {

    private val discover = DiscoverFragment()
    private val filters = FiltersFragment()
    private val mix = MixFragment()
    private val lists = ListsFragment()
    private var current: Fragment = discover

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        FilterState.load(this)

        if (savedInstanceState == null) {
            supportFragmentManager.beginTransaction().apply {
                add(R.id.fragmentHost, discover, "discover")
                add(R.id.fragmentHost, filters, "filters"); hide(filters)
                add(R.id.fragmentHost, mix, "mix"); hide(mix)
                add(R.id.fragmentHost, lists, "lists"); hide(lists)
            }.commit()
        }

        findViewById<BottomNavigationView>(R.id.bottomNav).setOnItemSelectedListener { item ->
            val target = when (item.itemId) {
                R.id.nav_discover -> discover
                R.id.nav_filters -> filters
                R.id.nav_mix -> mix
                else -> lists
            }
            show(target)
            true
        }

        // Nothing works without a server, so say so before the first request
        // fails in four different places.
        if (!Prefs(this).isConfigured) {
            startActivity(Intent(this, SettingsActivity::class.java))
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
        findViewById<BottomNavigationView>(R.id.bottomNav).selectedItemId = R.id.nav_discover
    }

    fun discoverFragment(): DiscoverFragment = discover

    override fun onPause() {
        super.onPause()
        FilterState.save(this)
    }
}
