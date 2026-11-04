package com.mangabrain.app.ui.lists

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.materialswitch.MaterialSwitch
import com.mangabrain.app.R
import com.mangabrain.app.data.FilterState
import com.mangabrain.app.data.ServiceLocator
import com.mangabrain.app.data.api.AppSettingsIn
import com.mangabrain.app.ui.SettingsActivity
import kotlinx.coroutines.launch

/**
 * The four list sources, their sync buttons, and their exclude toggles.
 *
 * Usernames live on the SERVER, not on the device: the web client and this
 * one both read and write GET/PUT /settings, so a list synced from either
 * is excluded in both. Only the server URL and access token are local,
 * because those describe how to reach the server rather than what is in it.
 *
 * MAL sync is expected to fail. Jikan discontinued its user list endpoints
 * in 2022, and the server says so in the error it returns; the button is
 * left in place so the message is reachable rather than mysterious.
 */
class ListsFragment : Fragment(R.layout.fragment_lists) {

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        bindExcludeToggles(view)

        view.findViewById<MaterialSwitch>(R.id.keepPlannedSwitch).apply {
            isChecked = FilterState.keepPlanned
            setOnCheckedChangeListener { _, checked -> FilterState.keepPlanned = checked }
        }

        view.findViewById<Button>(R.id.saveAccounts).setOnClickListener { saveAccounts() }
        view.findViewById<Button>(R.id.openServerSettings).setOnClickListener {
            startActivity(Intent(requireContext(), SettingsActivity::class.java))
        }

        view.findViewById<Button>(R.id.anilistSync).setOnClickListener {
            syncUserList(R.id.anilistUser, R.id.anilistStatus, anilist = true)
        }
        view.findViewById<Button>(R.id.malSync).setOnClickListener {
            syncUserList(R.id.malUser, R.id.malStatus, anilist = false)
        }
        view.findViewById<Button>(R.id.kitsuSync).setOnClickListener {
            syncNamedList(R.id.kitsuStatus, kitsu = true)
        }
        view.findViewById<Button>(R.id.yamtrackSync).setOnClickListener {
            syncNamedList(R.id.yamtrackStatus, kitsu = false)
        }

        loadSettings()
    }

    private fun bindExcludeToggles(view: View) {
        data class Toggle(val id: Int, val get: () -> Boolean, val set: (Boolean) -> Unit)
        for (toggle in listOf(
            Toggle(R.id.anilistExclude, { FilterState.excludeAnilist }, { FilterState.excludeAnilist = it }),
            Toggle(R.id.malExclude, { FilterState.excludeMal }, { FilterState.excludeMal = it }),
            Toggle(R.id.kitsuExclude, { FilterState.excludeKitsu }, { FilterState.excludeKitsu = it }),
            Toggle(R.id.yamtrackExclude, { FilterState.excludeYamtrack }, { FilterState.excludeYamtrack = it })
        )) {
            view.findViewById<MaterialSwitch>(toggle.id).apply {
                isChecked = toggle.get()
                setOnCheckedChangeListener { _, checked -> toggle.set(checked) }
            }
        }
    }

    private fun loadSettings() {
        val api = ServiceLocator.api(requireContext()) ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val settings = api.settings()
                FilterState.applySettings(settings)
                val view = view ?: return@launch
                view.findViewById<EditText>(R.id.anilistUser).setText(settings.anilist_username.orEmpty())
                view.findViewById<EditText>(R.id.malUser).setText(settings.mal_username.orEmpty())
                view.findViewById<EditText>(R.id.kitsuUser).setText(settings.kitsu_username.orEmpty())
                view.findViewById<EditText>(R.id.ytUrl).setText(settings.yamtrack_url.orEmpty())
                if (settings.yamtrack_token_set == true) {
                    view.findViewById<EditText>(R.id.ytToken).hint = getString(R.string.saved)
                }
            } catch (e: Exception) {
                status(R.id.accountsStatus, message(e))
            }
        }
    }

    private fun saveAccounts() {
        val api = ServiceLocator.api(requireContext()) ?: return
        val view = view ?: return
        fun text(id: Int) = view.findViewById<EditText>(id).text.toString().trim().ifBlank { null }
        status(R.id.accountsStatus, getString(R.string.checking))
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val saved = api.saveSettings(
                    AppSettingsIn(
                        anilist_username = text(R.id.anilistUser),
                        mal_username = text(R.id.malUser),
                        kitsu_username = text(R.id.kitsuUser),
                        yamtrack_url = text(R.id.ytUrl),
                        yamtrack_token = text(R.id.ytToken)
                    )
                )
                FilterState.applySettings(saved)
                // The token is write-only on the server; clearing the field
                // stops a later save from re-sending the same secret.
                view.findViewById<EditText>(R.id.ytToken).setText("")
                status(R.id.accountsStatus, getString(R.string.saved))
            } catch (e: Exception) {
                status(R.id.accountsStatus, message(e))
            }
        }
    }

    private fun syncUserList(userField: Int, statusField: Int, anilist: Boolean) {
        val api = ServiceLocator.api(requireContext()) ?: return
        val username = view?.findViewById<EditText>(userField)?.text?.toString()?.trim().orEmpty()
        if (username.isEmpty()) {
            status(statusField, getString(R.string.needs_username))
            return
        }
        status(statusField, getString(R.string.syncing))
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val result = if (anilist) api.refreshAnilist(username) else api.refreshMal(username)
                status(
                    statusField,
                    result.lists.joinToString(", ") { "${it.list_type}: ${it.entry_count} entries" }
                )
            } catch (e: Exception) {
                status(statusField, message(e))
            }
        }
    }

    private fun syncNamedList(statusField: Int, kitsu: Boolean) {
        val api = ServiceLocator.api(requireContext()) ?: return
        status(statusField, getString(R.string.syncing))
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val result = if (kitsu) api.refreshKitsu() else api.refreshYamtrack()
                status(statusField, getString(R.string.entries_cached, result.entry_count))
            } catch (e: Exception) {
                status(statusField, message(e))
            }
        }
    }

    /**
     * Server-supplied detail wins over a generic message. A refresh failure
     * usually carries the only useful explanation - "Jikan unreachable",
     * "MAL user not found" - and swallowing it for a tidy string would hide
     * exactly what the user has to act on.
     */
    private fun message(e: Exception): String = when (e) {
        is retrofit2.HttpException -> {
            val body = runCatching { e.response()?.errorBody()?.string() }.getOrNull()
            val detail = body?.let { Regex("\"detail\"\\s*:\\s*\"([^\"]+)\"").find(it)?.groupValues?.get(1) }
            detail ?: if (e.code() == 401) getString(R.string.error_token)
            else getString(R.string.error_http, e.code())
        }

        else -> getString(R.string.error_unreachable, e.message ?: "")
    }

    private fun status(id: Int, text: String) {
        view?.findViewById<TextView>(id)?.text = text
    }
}
