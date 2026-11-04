package com.mangabrain.app.ui

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.ImageView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import coil.load
import com.mangabrain.app.R
import com.mangabrain.app.data.ServiceLocator
import kotlinx.coroutines.launch

class RecommendActivity : AppCompatActivity() {

    private lateinit var adapter: MediaAdapter
    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_recommend)

        val mediaId = intent.getIntExtra(EXTRA_MEDIA_ID, 0)
        title = intent.getStringExtra(EXTRA_TITLE) ?: getString(R.string.app_name)
        status = findViewById(R.id.statusText)

        adapter = MediaAdapter { media ->
            // Same move as the web client: any result can become the seed.
            startActivity(
                Intent(this, RecommendActivity::class.java)
                    .putExtra(EXTRA_MEDIA_ID, media.id)
                    .putExtra(EXTRA_TITLE, media.displayTitle)
            )
        }
        val list: RecyclerView = findViewById(R.id.resultsList)
        list.layoutManager = LinearLayoutManager(this)
        list.adapter = adapter

        if (mediaId == 0) {
            show(getString(R.string.error_no_seed))
        } else {
            load(mediaId)
        }
    }

    private fun load(mediaId: Int) {
        val api = ServiceLocator.api(this)
        if (api == null) {
            show(getString(R.string.needs_server))
            return
        }
        show(getString(R.string.loading))
        lifecycleScope.launch {
            try {
                // Weights are the server's defaults (0.5 / 0.3 / 0.2). Sliders
                // are the next slice; the endpoint applies its defaults when
                // the parameters are absent.
                val data = api.recommend(mediaId, 50, false, true)
                findViewById<TextView>(R.id.seedTitle).text = data.seed.displayTitle
                findViewById<TextView>(R.id.seedMeta).text = data.seed.displayMeta
                data.seed.coverUrl?.let { findViewById<ImageView>(R.id.seedCover).load(it) }
                adapter.submit(
                    data.results.map { MediaAdapter.Row(it.media, it.similarity) }
                )
                show(if (data.results.isEmpty()) getString(R.string.no_recommendations) else null)
            } catch (e: retrofit2.HttpException) {
                show(
                    if (e.code() == 401) getString(R.string.error_token)
                    else getString(R.string.error_http, e.code())
                )
            } catch (e: Exception) {
                show(getString(R.string.error_unreachable, e.message ?: ""))
            }
        }
    }

    private fun show(message: String?) {
        status.text = message ?: ""
        status.visibility = if (message == null) View.GONE else View.VISIBLE
    }

    companion object {
        const val EXTRA_MEDIA_ID = "media_id"
        const val EXTRA_TITLE = "title"
    }
}
