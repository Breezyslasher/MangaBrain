package com.mangabrain.app.ui

import android.graphics.Typeface
import android.os.Bundle
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.materialswitch.MaterialSwitch
import com.mangabrain.app.R
import com.mangabrain.app.data.FilterState
import com.mangabrain.app.data.Prefs
import com.mangabrain.app.data.ServiceLocator
import com.mangabrain.app.data.api.AppSettingsIn
import com.mangabrain.app.data.api.DatasetOut
import com.mangabrain.app.data.api.httpMessage
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.text.NumberFormat

class SettingsActivity : AppCompatActivity() {

    /** Refreshes the media section while an install is under way. */
    private var mediaJob: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        title = getString(R.string.settings)

        val prefs = Prefs(this)
        val urlInput: EditText = findViewById(R.id.serverUrlInput)
        val tokenInput: EditText = findViewById(R.id.tokenInput)
        val status: TextView = findViewById(R.id.statusText)

        urlInput.setText(prefs.serverUrl?.trimEnd('/') ?: "")
        tokenInput.setText(prefs.token ?: "")

        findViewById<Button>(R.id.saveButton).setOnClickListener {
            val url = urlInput.text.toString().trim()
            if (url.isEmpty()) {
                status.text = getString(R.string.error_url_required)
                return@setOnClickListener
            }
            if (!url.startsWith("http://") && !url.startsWith("https://")) {
                status.text = getString(R.string.error_url_scheme)
                return@setOnClickListener
            }
            prefs.serverUrl = url
            // Blank clears it, which is the normal state for a LAN server.
            prefs.token = tokenInput.text.toString().trim().ifBlank { null }
            ServiceLocator.invalidate()
            status.text = getString(R.string.checking)
            verify(status)
            loadMedia()
        }
    }

    override fun onResume() {
        super.onResume()
        loadMedia()
    }

    override fun onPause() {
        super.onPause()
        mediaJob?.cancel()
    }

    // ------------------------------------------------------------- media
    //
    // Nothing here names a medium. The datasets, the media each fills and
    // what installing costs all come from GET /datasets, so a medium added
    // on the server appears here without an app update. Only the tab labels
    // are local, and an unknown key falls back to showing the key.

    private fun loadMedia() {
        mediaJob?.cancel()
        mediaJob = lifecycleScope.launch {
            val api = ServiceLocator.api(this@SettingsActivity)
            if (api == null) {
                mediaStatus(getString(R.string.needs_server))
                return@launch
            }
            while (isActive) {
                val active = try {
                    FilterState.applySettings(api.settings())
                    val datasets = api.datasets().datasets
                    renderMedia(datasets)
                    datasets.any { it.install?.state in ACTIVE }
                } catch (e: retrofit2.HttpException) {
                    mediaStatus(httpMessage(this@SettingsActivity, e))
                    false
                } catch (e: Exception) {
                    mediaStatus(getString(R.string.error_unreachable, e.message ?: ""))
                    false
                }
                if (!active) break
                delay(4000)
            }
        }
    }

    private fun renderMedia(datasets: List<DatasetOut>) {
        val container: LinearLayout = findViewById(R.id.mediaContainer)
        container.removeAllViews()
        val enabled = FilterState.visibleMedia()
        val text = ContextCompat.getColor(this, R.color.text)
        val muted = ContextCompat.getColor(this, R.color.muted)
        for (dataset in datasets) {
            container.addView(TextView(this).apply {
                setText(dataset.label)
                setTextColor(text)
                setTypeface(typeface, Typeface.BOLD)
                textSize = 16f
                setPadding(0, dp(16), 0, dp(2))
            })
            container.addView(TextView(this).apply {
                setText(dataset.description)
                setTextColor(muted)
                textSize = 12f
            })
            container.addView(TextView(this).apply {
                setText(installText(dataset))
                setTextColor(muted)
                textSize = 13f
                setPadding(0, dp(4), 0, dp(4))
            })
            val install = dataset.install
            val total = install?.total ?: 0L
            if (install != null && install.state == "running" && install.phase == "downloading" && total > 0) {
                val done = install.bytes ?: 0L
                container.addView(
                    ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
                        max = 1000
                        progress = (done * 1000 / total).toInt()
                    },
                    LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(8))
                )
            }
            for (group in dataset.media) {
                container.addView(MaterialSwitch(this).apply {
                    setText(FilterState.MEDIUM_LABELS[group] ?: group)
                    setTextColor(text)
                    isChecked = group in enabled
                    minHeight = dp(48)
                    // A click, not a checked-change listener: reverting the
                    // switch after a cancelled download must not count as
                    // the reader switching it off.
                    setOnClickListener { toggleMedium(group, isChecked, dataset, this) }
                })
            }
            val busy = install?.state in ACTIVE
            if (dataset.installable && !busy && (install?.state == "failed" || dataset.installed)) {
                container.addView(Button(this).apply {
                    setText(if (install?.state == "failed") R.string.media_retry else R.string.media_update)
                    minHeight = dp(48)
                    setOnClickListener { install(dataset) }
                })
            }
        }
    }

    /** The same wording the web settings page uses, for the same states. */
    private fun installText(dataset: DatasetOut): String {
        val count = NumberFormat.getIntegerInstance()
        val install = dataset.install
        if (install != null) {
            val total = install.total ?: 0L
            when (install.state) {
                "running" -> return when (install.phase) {
                    "downloading" -> if (total > 0) {
                        getString(R.string.media_downloading, gb(install.bytes ?: 0L), gb(total))
                    } else getString(R.string.media_starting)
                    "merging" -> getString(R.string.media_merging)
                    "indexing" -> getString(R.string.media_indexing)
                    else -> getString(R.string.media_starting)
                }
                "queued" -> return getString(R.string.media_queued)
                "waiting", "failed" -> return install.message.orEmpty()
            }
        }
        return when {
            dataset.installed && !dataset.installable ->
                getString(R.string.media_titles, count.format(dataset.items))
            dataset.installed ->
                getString(R.string.media_installed, count.format(dataset.items), count.format(dataset.embedded))
            dataset.release_available == false -> getString(R.string.media_not_published)
            else -> getString(R.string.media_not_downloaded, dataset.download_hint.orEmpty())
        }
    }

    private fun toggleMedium(group: String, on: Boolean, dataset: DatasetOut, switch: MaterialSwitch) {
        val current = FilterState.visibleMedia()
        val next = if (on) (current + group).distinct() else current - group
        if (next.isEmpty()) {
            switch.isChecked = true
            mediaStatus(getString(R.string.media_keep_one))
            return
        }
        val willDownload = on && dataset.installable && !dataset.installed &&
            dataset.install?.state !in ACTIVE
        if (!willDownload) {
            saveMedia(next, switch, on)
            return
        }
        // Asked before, never after: switching this on is what starts it.
        AlertDialog.Builder(this)
            .setTitle(getString(R.string.media_download_title, dataset.label))
            .setMessage(dataset.download_hint)
            .setPositiveButton(R.string.media_download) { _, _ -> saveMedia(next, switch, on) }
            .setNegativeButton(android.R.string.cancel) { _, _ -> switch.isChecked = false }
            .setOnCancelListener { switch.isChecked = false }
            .show()
    }

    private fun saveMedia(next: List<String>, switch: MaterialSwitch, on: Boolean) {
        val api = ServiceLocator.api(this) ?: return
        mediaStatus(getString(R.string.media_saving))
        lifecycleScope.launch {
            try {
                FilterState.applySettings(api.saveSettings(AppSettingsIn(enabled_media = next)))
                mediaStatus("")
            } catch (e: retrofit2.HttpException) {
                switch.isChecked = !on
                mediaStatus(httpMessage(this@SettingsActivity, e))
            } catch (e: Exception) {
                switch.isChecked = !on
                mediaStatus(getString(R.string.error_unreachable, e.message ?: ""))
            }
            loadMedia()
        }
    }

    private fun install(dataset: DatasetOut) {
        val api = ServiceLocator.api(this) ?: return
        lifecycleScope.launch {
            try {
                api.installDataset(dataset.key)
            } catch (e: retrofit2.HttpException) {
                mediaStatus(httpMessage(this@SettingsActivity, e))
            } catch (e: Exception) {
                mediaStatus(getString(R.string.error_unreachable, e.message ?: ""))
            }
            loadMedia()
        }
    }

    private fun mediaStatus(message: String) {
        findViewById<TextView>(R.id.mediaStatus).text = message
    }

    private fun gb(bytes: Long): String = String.format("%.2f GB", bytes / 1e9)

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private companion object {
        val ACTIVE = setOf("queued", "running", "waiting")
    }

    /**
     * Saving is not the same as working, so the settings screen proves the
     * server answers before the user walks away from it. A wrong URL, a
     * server that is down and a server that wants a token all look
     * identical from the search screen otherwise.
     */
    private fun verify(status: TextView) {
        lifecycleScope.launch {
            val api = ServiceLocator.api(this@SettingsActivity)
            if (api == null) {
                status.text = getString(R.string.error_url_required)
                return@launch
            }
            try {
                // One row is enough to prove the server answers; the
                // filter surface is irrelevant to a connectivity check.
                val response = api.search(null, mapOf("limit" to "1"))
                status.text = getString(R.string.connected, response.results.size)
            } catch (e: retrofit2.HttpException) {
                status.text = when (e.code()) {
                    401 -> getString(R.string.error_token)
                    404 -> getString(R.string.error_not_mangabrain)
                    else -> httpMessage(this@SettingsActivity, e)
                }
            } catch (e: Exception) {
                status.text = getString(R.string.error_unreachable, e.message ?: "")
            }
        }
    }
}
