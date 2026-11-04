package com.mangabrain.app.ui

import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.mangabrain.app.R
import com.mangabrain.app.data.Prefs
import com.mangabrain.app.data.ServiceLocator
import com.mangabrain.app.data.api.httpMessage
import kotlinx.coroutines.launch

class SettingsActivity : AppCompatActivity() {

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
        }
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
