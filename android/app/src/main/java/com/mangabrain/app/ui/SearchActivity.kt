package com.mangabrain.app.ui

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.doAfterTextChanged
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.mangabrain.app.R
import com.mangabrain.app.data.Prefs
import com.mangabrain.app.data.ServiceLocator
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class SearchActivity : AppCompatActivity() {

    private lateinit var adapter: MediaAdapter
    private lateinit var status: TextView
    private var searchJob: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_search)

        status = findViewById(R.id.statusText)
        val input: EditText = findViewById(R.id.searchInput)
        val list: RecyclerView = findViewById(R.id.resultsList)

        adapter = MediaAdapter { media ->
            startActivity(
                Intent(this, RecommendActivity::class.java)
                    .putExtra(RecommendActivity.EXTRA_MEDIA_ID, media.id)
                    .putExtra(RecommendActivity.EXTRA_TITLE, media.displayTitle)
            )
        }
        list.layoutManager = LinearLayoutManager(this)
        list.adapter = adapter

        findViewById<Button>(R.id.settingsButton).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        // 300ms, matching the web client's debounce, so a fast typist sends
        // one request rather than one per keystroke.
        input.doAfterTextChanged { text ->
            searchJob?.cancel()
            val query = text?.toString()?.trim().orEmpty()
            if (query.isEmpty()) {
                adapter.submit(emptyList())
                show(null)
                return@doAfterTextChanged
            }
            searchJob = lifecycleScope.launch {
                delay(300)
                runSearch(query)
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // First run, or the user cleared the URL: there is nothing to search.
        if (!Prefs(this).isConfigured) {
            show(getString(R.string.needs_server))
            startActivity(Intent(this, SettingsActivity::class.java))
        }
    }

    private suspend fun runSearch(query: String) {
        val api = ServiceLocator.api(this)
        if (api == null) {
            show(getString(R.string.needs_server))
            return
        }
        show(getString(R.string.searching))
        try {
            // medium is left null so the search spans every medium; the web
            // client scopes it by its tab, which this slice has no UI for.
            val response = api.search(query, null, 30, false)
            adapter.submit(response.results.map { MediaAdapter.Row(it) })
            show(if (response.results.isEmpty()) getString(R.string.no_matches) else null)
        } catch (e: retrofit2.HttpException) {
            adapter.submit(emptyList())
            show(
                if (e.code() == 401) getString(R.string.error_token)
                else getString(R.string.error_http, e.code())
            )
        } catch (e: Exception) {
            adapter.submit(emptyList())
            show(getString(R.string.error_unreachable, e.message ?: ""))
        }
    }

    private fun show(message: String?) {
        status.text = message ?: ""
        status.visibility = if (message == null) View.GONE else View.VISIBLE
    }
}
