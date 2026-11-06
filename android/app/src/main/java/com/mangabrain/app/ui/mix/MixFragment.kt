package com.mangabrain.app.ui.mix

import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.mangabrain.app.R
import com.mangabrain.app.data.MixStore
import com.mangabrain.app.ui.MainActivity
import com.mangabrain.app.ui.MixAdapter

class MixFragment : Fragment(R.layout.fragment_mix) {

    private lateinit var adapter: MixAdapter

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        adapter = MixAdapter { id ->
            MixStore.remove(id)
            refresh()
        }
        view.findViewById<RecyclerView>(R.id.mixList).apply {
            layoutManager = LinearLayoutManager(requireContext())
            this.adapter = this@MixFragment.adapter
        }

        view.findViewById<Button>(R.id.mixGo).setOnClickListener {
            if (MixStore.size == 0) return@setOnClickListener
            (activity as? MainActivity)?.let { main ->
                main.showDiscover()
                main.discoverFragment().loadMix()
            }
        }

        refresh()
    }

    /** Called when the tab is shown: the mix changes from the Discover grid. */
    fun refresh() {
        val view = view ?: return
        adapter.submit(MixStore.items)
        view.findViewById<View>(R.id.mixEmpty).visibility =
            if (MixStore.size == 0) View.VISIBLE else View.GONE
        view.findViewById<Button>(R.id.mixGo).isEnabled = MixStore.size > 0
        view.findViewById<TextView>(R.id.mixStatus).text =
            if (MixStore.size == 0) "" else "${MixStore.size} / ${MixStore.MAX}"
    }
}
