package com.mangabrain.app.ui.settings

import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AlertDialog
import androidx.core.content.ContextCompat
import androidx.core.content.res.ResourcesCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.materialswitch.MaterialSwitch
import com.mangabrain.app.R
import com.mangabrain.app.data.FilterState
import com.mangabrain.app.data.Prefs
import com.mangabrain.app.data.ServiceLocator
import com.mangabrain.app.data.api.AppSettingsIn
import com.mangabrain.app.data.api.DatasetOut
import com.mangabrain.app.data.api.ExclusionStatus
import com.mangabrain.app.data.api.MangaBrainApi
import com.mangabrain.app.data.api.UserListsStatus
import com.mangabrain.app.data.api.httpMessage
import com.mangabrain.app.ui.MainActivity
import com.mangabrain.app.ui.discover.ForYouPrefs
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.text.NumberFormat
import java.util.Calendar
import java.util.TimeZone

/**
 * The Settings tab (design 3b): a short home page of rows, each with a
 * live one-line summary, and a page behind each row.
 *
 * It replaces the Lists tab and the separate server screen. Everything a
 * reader sets once rather than per search lives here: the four list
 * accounts and their exclude toggles, keep planned, the For-you feed,
 * which media this server holds, the title language, and how to reach the
 * server.
 *
 * Pages are views inside this one fragment, on a stack. Android's back
 * button and the arrow in each page's top bar both pop it, and the tab bar
 * is hidden on every page but home, where it would offer a way out that
 * skips the stack.
 *
 * Every page is rebuilt from state each time it is shown, rather than kept
 * alive and patched. The state is small, the pages are short, and a page
 * that is always built from the truth cannot show a stale summary.
 */
class SettingsFragment : Fragment(R.layout.fragment_settings) {

    enum class Page { HOME, ACCOUNTS, ACCOUNT, EXCLUDE, FOR_YOU, MEDIA, DISPLAY, SERVER }

    /** The four list sources. Badge colours are the 3b palette. */
    /**
     * Each list's own logo on its brand colour. Yamtrack's mark is its full
     * app icon (a PNG with its own dark square), so it is drawn as is and
     * [tinted] is false; the other three are white Simple Icons glyphs.
     */
    enum class Account(
        val labelRes: Int, val logoRes: Int, val bgRes: Int, val tinted: Boolean = true
    ) {
        ANILIST(R.string.account_anilist, R.drawable.logo_anilist, R.color.brand_anilist),
        MAL(R.string.account_mal, R.drawable.logo_myanimelist, R.color.brand_mal),
        KITSU(R.string.account_kitsu, R.drawable.logo_kitsu, R.color.brand_kitsu),
        YAMTRACK(R.string.account_yamtrack, R.drawable.logo_yamtrack, R.color.brand_yamtrack, tinted = false),
        // Token-only, like Yamtrack's token: no username, so "connected"
        // means a token is saved. Its mark keeps its own indigo, inset on
        // the pale tint it is drawn on.
        HARDCOVER(R.string.account_hardcover, R.drawable.logo_hardcover, R.color.brand_hardcover)
    }

    /** What the server last reported for a synced list. */
    private data class ListStatus(val syncedAt: Long?, val tiles: List<Pair<String, Int>>)

    private val stack = ArrayDeque<Page>()
    private var account = Account.ANILIST

    /** Usernames from GET /settings; Yamtrack's entry is its URL. */
    private val usernames = mutableMapOf<Account, String>()
    private var yamtrackTokenSet = false
    private var hardcoverTokenSet = false
    private var tmdbKeySet = false
    private val statuses = mutableMapOf<Account, ListStatus>()
    private var statusesLoaded = false
    private var settingsLoaded = false
    private var datasets: List<DatasetOut> = emptyList()

    private var pollJob: Job? = null
    private var accountStatus: TextView? = null
    private var accountStats: LinearLayout? = null

    private val back = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() = pop()
    }

    // ------------------------------------------------------------ lifecycle

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        requireActivity().onBackPressedDispatcher.addCallback(viewLifecycleOwner, back)
        if (stack.isEmpty()) stack.addLast(Page.HOME)
        render()
        loadSettings()
    }

    /**
     * Every tab is added at app start and shown or hidden, so this is the
     * "page appeared" signal rather than onResume. It also keeps the back
     * button from popping Settings pages while another tab is on screen.
     */
    override fun onHiddenChanged(hidden: Boolean) {
        super.onHiddenChanged(hidden)
        if (hidden) {
            pollJob?.cancel()
            syncChrome()
            return
        }
        if (!settingsLoaded) loadSettings()
        statusesLoaded = false
        render()
    }

    /** First run: nothing works without a server, so open on that page. */
    fun openServer() {
        stack.clear()
        stack.addLast(Page.HOME)
        stack.addLast(Page.SERVER)
        if (view != null) render()
    }

    // ----------------------------------------------------------- navigation

    private fun open(page: Page) {
        stack.addLast(page)
        render()
    }

    private fun pop() {
        if (stack.size > 1) {
            stack.removeLast()
            render()
        }
    }

    private fun syncChrome() {
        val top = stack.lastOrNull() ?: Page.HOME
        back.isEnabled = !isHidden && stack.size > 1
        (activity as? MainActivity)?.setTabBarVisible(isHidden || top == Page.HOME)
    }

    private fun render() {
        val root = view?.findViewById<FrameLayout>(R.id.settingsHost) ?: return
        val page = stack.last()
        pollJob?.cancel()
        accountStatus = null
        accountStats = null
        root.removeAllViews()
        val pageView = layoutInflater.inflate(R.layout.page_settings, root, false)
        root.addView(pageView)
        val body = pageView.findViewById<LinearLayout>(R.id.pageBody)
        val actions = pageView.findViewById<LinearLayout>(R.id.pageActions)
        val topbar = pageView.findViewById<View>(R.id.topbar)
        if (page == Page.HOME) {
            topbar.visibility = View.GONE
        } else {
            pageView.findViewById<TextView>(R.id.topbarTitle).text = titleOf(page)
            pageView.findViewById<ImageButton>(R.id.topbarBack).setOnClickListener { pop() }
        }
        when (page) {
            Page.HOME -> buildHome(body)
            Page.ACCOUNTS -> buildAccounts(body)
            Page.ACCOUNT -> buildAccount(body, actions, pageView.findViewById(R.id.pageScroll))
            Page.EXCLUDE -> buildExclude(body)
            Page.FOR_YOU -> buildForYou(body)
            Page.MEDIA -> buildMedia(body)
            Page.DISPLAY -> buildDisplay(body)
            Page.SERVER -> buildServer(body)
        }
        syncChrome()
    }

    private fun titleOf(page: Page): String = when (page) {
        Page.HOME -> getString(R.string.tab_settings)
        Page.ACCOUNTS -> getString(R.string.row_accounts)
        Page.ACCOUNT -> getString(account.labelRes)
        Page.EXCLUDE -> getString(R.string.row_exclude)
        Page.FOR_YOU -> getString(R.string.row_for_you)
        Page.MEDIA -> getString(R.string.row_media)
        Page.DISPLAY -> getString(R.string.row_display)
        Page.SERVER -> getString(R.string.row_server)
    }

    // ----------------------------------------------------------------- home

    private fun buildHome(body: LinearLayout) {
        body.addView(TextView(requireContext()).apply {
            text = getString(R.string.tab_settings)
            setTextColor(color(R.color.text))
            textSize = 32f
            setTypeface(typeface, Typeface.BOLD)
            letterSpacing = -0.03f
            setPadding(dp(22), dp(26), dp(22), dp(18))
        })
        body.addView(syncCard())

        label(body, R.string.group_lists)
        card(body).apply {
            addView(navRow(R.drawable.mdi_account_multiple_outline, R.color.accent,
                getString(R.string.row_accounts), accountsSummary()) { open(Page.ACCOUNTS) })
            addView(navRow(R.drawable.mdi_eye_off_outline, R.color.bad,
                getString(R.string.row_exclude), excludeSummary()) { open(Page.EXCLUDE) })
        }
        label(body, R.string.group_recommendations)
        card(body).apply {
            addView(navRow(R.drawable.mdi_star_shooting_outline, R.color.ok,
                getString(R.string.row_for_you), forYouSummary()) { open(Page.FOR_YOU) })
            addView(navRow(R.drawable.mdi_bookshelf, R.color.w_genres,
                getString(R.string.row_media), mediaSummary()) { open(Page.MEDIA) })
        }
        label(body, R.string.group_app)
        card(body).apply {
            addView(navRow(R.drawable.mdi_translate, R.color.muted,
                getString(R.string.row_display), displaySummary()) { open(Page.DISPLAY) })
            addView(navRow(R.drawable.mdi_key_variant, R.color.muted,
                getString(R.string.row_server), serverSummary()) { open(Page.SERVER) })
        }
        loadStatuses()
        loadDatasetsForSummary()
    }

    /** "2 of 4 lists connected", last sync, and Sync all (3b home). */
    private fun syncCard(): View {
        val connected = Account.values().filter { connected(it) }
        val card = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = ContextCompat.getDrawable(context, R.drawable.bg_sync_card)
            setPadding(dp(14), dp(14), dp(14), dp(14))
            layoutParams = LinearLayout.LayoutParams(MATCH, WRAP).apply {
                setMargins(dp(14), 0, dp(14), dp(18))
            }
        }
        val badges = LinearLayout(requireContext()).apply { orientation = LinearLayout.HORIZONTAL }
        for (a in connected) {
            badges.addView(accountBadge(a, 34, circle = true).apply {
                layoutParams = LinearLayout.LayoutParams(dp(34), dp(34)).apply { marginEnd = -dp(8) }
            })
        }
        card.addView(badges)
        val texts = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(if (connected.isEmpty()) 0 else dp(18), 0, dp(10), 0)
            layoutParams = LinearLayout.LayoutParams(0, WRAP, 1f)
        }
        texts.addView(text(15f, R.color.text, bold = true).apply {
            text = getString(R.string.lists_connected, connected.size, Account.values().size)
        })
        val latest = connected.mapNotNull { statuses[it]?.syncedAt }.maxOrNull()
        texts.addView(text(12.5f, R.color.seed_meta).apply {
            text = when {
                connected.isEmpty() -> getString(R.string.connect_a_list)
                latest != null -> getString(R.string.last_sync, timeAgo(latest))
                else -> getString(R.string.not_synced_yet)
            }
            setPadding(0, dp(2), 0, 0)
        })
        card.addView(texts)
        card.addView(pill(getString(R.string.sync_all), compact = true).apply {
            isEnabled = connected.isNotEmpty()
            setOnClickListener { syncAll() }
        })
        return card
    }

    private fun accountsSummary(): String {
        val names = Account.values().filter { connected(it) }.map { getString(it.labelRes) }
        return if (names.isEmpty()) getString(R.string.summary_none_connected)
        else getString(R.string.summary_connected, names.joinToString(", "))
    }

    private fun excludeSummary(): String {
        val names = Account.values().filter { excluded(it) }.map { getString(it.labelRes) }
        val lists = if (names.isEmpty()) getString(R.string.summary_none_excluded) else names.joinToString(", ")
        val planned = getString(
            if (FilterState.keepPlanned) R.string.summary_keep_planned_on else R.string.summary_keep_planned_off
        )
        return "$lists · $planned"
    }

    private fun forYouSummary(): String = getString(
        R.string.summary_for_you,
        ForYouPrefs.seeds,
        getString(if (ForYouPrefs.useRatings) R.string.weighted_by_ratings else R.string.unweighted)
    )

    /** The number of tabs on, and any install under way (3b). */
    private fun mediaSummary(): String {
        val tabs = getString(R.string.summary_tabs, FilterState.visibleMedia().size)
        val busy = datasets.firstOrNull { it.install?.state in ACTIVE } ?: return tabs
        val install = busy.install ?: return tabs
        val total = install.total ?: 0L
        val detail = when {
            install.state == "running" && install.phase == "downloading" && total > 0 ->
                getString(R.string.summary_downloading, busy.label, ((install.bytes ?: 0L) * 100 / total).toInt())
            install.state == "running" -> getString(R.string.summary_installing, busy.label)
            install.state == "queued" -> getString(R.string.summary_queued, busy.label)
            else -> getString(R.string.summary_waiting, busy.label)
        }
        return "$tabs · $detail"
    }

    private fun displaySummary(): String =
        getString(if (FilterState.romajiTitles) R.string.titles_romaji else R.string.titles_english)

    private fun serverSummary(): String {
        val prefs = Prefs(requireContext())
        return when {
            !prefs.isConfigured -> getString(R.string.server_not_set)
            !prefs.token.isNullOrBlank() -> getString(R.string.token_saved)
            else -> getString(R.string.token_none)
        }
    }

    // ------------------------------------------------------------- accounts

    private fun buildAccounts(body: LinearLayout) {
        card(body).apply {
            for (a in Account.values()) {
                val summary = when {
                    !connected(a) -> getString(R.string.not_connected)
                    statuses[a]?.syncedAt != null -> listOfNotNull(
                        usernames[a]?.takeIf { a == Account.ANILIST || a == Account.MAL || a == Account.KITSU },
                        getString(R.string.synced_ago, timeAgo(statuses[a]!!.syncedAt!!))
                    ).joinToString(" · ")
                    else -> usernames[a].orEmpty()
                }
                addView(row(accountBadge(a, 36, circle = false), getString(a.labelRes), summary) {
                    account = a
                    open(Page.ACCOUNT)
                })
            }
        }
        loadStatuses()
    }

    /** One template for all four (3b page 2). */
    private fun buildAccount(body: LinearLayout, actions: LinearLayout, scroll: ScrollView) {
        val a = account
        // Room for the pinned action bar, so the last field is never under it.
        scroll.setPadding(0, 0, 0, dp(120))

        val header = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(0, dp(18), 0, dp(22))
        }
        header.addView(accountBadge(a, 68, circle = false, radius = 20f).apply {
            layoutParams = LinearLayout.LayoutParams(dp(68), dp(68))
        })
        header.addView(text(20f, R.color.text, bold = true).apply {
            text = if (connected(a)) displayName(a) else getString(R.string.not_connected)
            setPadding(0, dp(8), 0, 0)
        })
        val status = text(13f, R.color.muted).apply { setPadding(dp(16), dp(4), dp(16), 0); gravity = Gravity.CENTER }
        header.addView(status)
        body.addView(header)
        accountStatus = status

        val stats = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(MATCH, WRAP).apply { setMargins(dp(14), 0, dp(14), dp(16)) }
        }
        body.addView(stats)
        accountStats = stats
        showAccountStatus(a)

        label(body, R.string.section_account)
        val fields = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(MATCH, WRAP).apply { setMargins(dp(14), 0, dp(14), dp(16)) }
        }
        body.addView(fields)
        val primary: EditText
        if (a == Account.YAMTRACK) {
            primary = field(fields, R.string.label_server_url,
                InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
            primary.setText(usernames[a].orEmpty())
            primary.hint = getString(R.string.hint_yamtrack_url)
            val token = field(fields, R.string.label_api_token,
                InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
            token.hint = getString(if (yamtrackTokenSet) R.string.saved else R.string.hint_yamtrack_token)
            // Save on blur. The token is write-only: an empty field means
            // "unchanged", never "clear it".
            token.setOnFocusChangeListener { _, focused ->
                val value = token.text.toString().trim()
                if (!focused && value.isNotEmpty()) {
                    saveAccount(a, yamtrackToken = value) {
                        token.setText("")
                        token.hint = getString(R.string.saved)
                    }
                }
            }
        } else if (a == Account.HARDCOVER) {
            primary = field(fields, R.string.label_api_token,
                InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
            primary.hint = getString(if (hardcoverTokenSet) R.string.saved else R.string.hint_hardcover_token)
            // Write-only, like Yamtrack's: an empty field means unchanged.
            // Before the first save, Connect saves it instead.
            primary.setOnFocusChangeListener { _, focused ->
                val value = primary.text.toString().trim()
                if (!focused && connected(a) && value.isNotEmpty()) {
                    saveAccount(a, value = value) {
                        primary.setText("")
                        primary.hint = getString(R.string.saved)
                    }
                }
            }
        } else {
            primary = field(fields, R.string.label_username, InputType.TYPE_CLASS_TEXT)
            primary.setText(usernames[a].orEmpty())
            primary.hint = getString(R.string.hint_username)
        }
        if (a != Account.HARDCOVER) primary.setOnFocusChangeListener { _, focused ->
            val value = primary.text.toString().trim()
            if (!focused && connected(a) && value.isNotEmpty() && value != usernames[a]) {
                saveAccount(a, value = value)
            }
        }

        label(body, R.string.section_in_results)
        card(body).addView(switchRow(getString(R.string.exclude_this_list), getString(R.string.exclude_hint),
            excluded(a)) { setExcluded(a, it) })

        actions.visibility = View.VISIBLE
        if (connected(a)) {
            actions.addView(pill(getString(R.string.disconnect), bad = true).apply {
                layoutParams = LinearLayout.LayoutParams(0, WRAP, 1f).apply { marginEnd = dp(10) }
                setOnClickListener { disconnect(a) }
            })
            actions.addView(pill(getString(R.string.sync_now)).apply {
                layoutParams = LinearLayout.LayoutParams(0, WRAP, 1.4f)
                setOnClickListener { syncAccount(a) }
            })
        } else {
            actions.addView(pill(getString(R.string.connect)).apply {
                layoutParams = LinearLayout.LayoutParams(0, WRAP, 1f)
                setOnClickListener {
                    val value = primary.text.toString().trim()
                    if (value.isEmpty()) {
                        toast(getString(if (a == Account.HARDCOVER) R.string.needs_token else R.string.needs_username))
                        return@setOnClickListener
                    }
                    saveAccount(a, value = value) { syncAccount(a) }
                }
            })
        }
    }

    private fun showAccountStatus(a: Account) {
        val status = accountStatus ?: return
        val stats = accountStats ?: return
        val synced = statuses[a]?.syncedAt
        if (synced != null) {
            status.text = getString(R.string.synced_ago, timeAgo(synced))
            status.setTextColor(color(R.color.ok))
        } else {
            status.text = getString(R.string.not_synced)
            status.setTextColor(color(R.color.muted))
        }
        stats.removeAllViews()
        val tiles = statuses[a]?.tiles.orEmpty()
        stats.visibility = if (tiles.isEmpty()) View.GONE else View.VISIBLE
        tiles.forEachIndexed { i, (name, count) ->
            stats.addView(LinearLayout(requireContext()).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER
                background = ContextCompat.getDrawable(context, R.drawable.bg_card)
                setPadding(dp(12), dp(12), dp(12), dp(12))
                layoutParams = LinearLayout.LayoutParams(0, WRAP, 1f).apply {
                    if (i > 0) marginStart = dp(8)
                }
                addView(text(19f, R.color.text, bold = true).apply {
                    typeface = mono()
                    text = NumberFormat.getIntegerInstance().format(count)
                    gravity = Gravity.CENTER
                })
                addView(text(11.5f, R.color.muted).apply {
                    text = name
                    gravity = Gravity.CENTER
                    setPadding(0, dp(3), 0, 0)
                })
            })
        }
    }

    private fun disconnect(a: Account) {
        saveAccount(a, value = "") {
            statuses.remove(a)
            render()
        }
    }

    /**
     * Saves one account and nothing else. AppSettingsIn's nulls are left out
     * of the body, so the other three accounts are untouched; an empty
     * string clears this one on the server.
     */
    private fun saveAccount(
        a: Account,
        value: String? = null,
        yamtrackToken: String? = null,
        then: (() -> Unit)? = null
    ) {
        val api = api() ?: return
        val body = when (a) {
            Account.ANILIST -> AppSettingsIn(anilist_username = value)
            Account.MAL -> AppSettingsIn(mal_username = value)
            Account.KITSU -> AppSettingsIn(kitsu_username = value)
            Account.YAMTRACK -> AppSettingsIn(yamtrack_url = value, yamtrack_token = yamtrackToken)
            Account.HARDCOVER -> AppSettingsIn(hardcover_token = value)
        }
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val saved = api.saveSettings(body)
                FilterState.applySettings(saved)
                readUsernames(saved)
                toast(getString(R.string.saved))
                then?.invoke()
            } catch (e: Exception) {
                toast(message(e))
            }
        }
    }

    private fun syncAccount(a: Account, after: (() -> Unit)? = null) {
        val api = api() ?: return
        accountStatus?.let {
            it.text = getString(R.string.syncing)
            it.setTextColor(color(R.color.accent_text))
        }
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                statuses[a] = when (a) {
                    Account.ANILIST -> fromLists(api.refreshAnilist(usernames[a].orEmpty()))
                    Account.MAL -> fromLists(api.refreshMal(usernames[a].orEmpty()))
                    Account.KITSU -> fromExclusion(api.refreshKitsu())
                    Account.YAMTRACK -> fromExclusion(api.refreshYamtrack())
                    Account.HARDCOVER -> fromExclusion(api.refreshHardcover())
                }
                if (stack.last() == Page.ACCOUNT && account == a) {
                    // A Connect turns into Disconnect / Sync now.
                    render()
                }
            } catch (e: Exception) {
                accountStatus?.let {
                    it.text = message(e)
                    it.setTextColor(color(R.color.bad))
                }
                toast(message(e))
            }
            after?.invoke()
        }
    }

    private fun syncAll() {
        val queue = Account.values().filter { connected(it) }.toMutableList()
        fun next() {
            val a = queue.removeFirstOrNull()
            if (a == null) {
                if (stack.last() == Page.HOME) render()
                return
            }
            syncAccount(a) { next() }
        }
        toast(getString(R.string.syncing))
        next()
    }

    private fun loadStatuses() {
        // Not before /settings has answered: with no usernames known this
        // would find nothing connected and mark itself done.
        if (statusesLoaded || !settingsLoaded) return
        val api = api(quiet = true) ?: return
        statusesLoaded = true
        viewLifecycleOwner.lifecycleScope.launch {
            var changed = false
            for (a in Account.values().filter { connected(it) }) {
                val status = fetchStatus(api, a) ?: continue
                if (statuses[a] != status) {
                    statuses[a] = status
                    changed = true
                }
            }
            val top = stack.last()
            if (changed && (top == Page.HOME || top == Page.ACCOUNTS)) render()
        }
    }

    /** A list never synced answers 404; that is "not synced", not an error. */
    private suspend fun fetchStatus(api: MangaBrainApi, a: Account): ListStatus? = try {
        when (a) {
            Account.ANILIST -> fromLists(api.anilistStatus(usernames[a].orEmpty()))
            Account.MAL -> fromLists(api.malStatus(usernames[a].orEmpty()))
            Account.KITSU -> fromExclusion(api.kitsuStatus())
            Account.YAMTRACK -> fromExclusion(api.yamtrackStatus())
            Account.HARDCOVER -> fromExclusion(api.hardcoverStatus())
        }
    } catch (e: Exception) {
        null
    }

    /**
     * One tile per list the server returned. The refresh response carries
     * a list type and an entry count, not a completed / watching / planning
     * breakdown, so the tiles are what it can honestly show: Anime, Manga.
     */
    private fun fromLists(status: UserListsStatus): ListStatus = ListStatus(
        status.lists.mapNotNull { parseIso(it.fetched_at) }.maxOrNull(),
        status.lists.map { it.list_type.replaceFirstChar { c -> c.uppercase() } to it.entry_count }
    )

    /** Kitsu, Yamtrack and Hardcover report one count, so one tile. */
    private fun fromExclusion(status: ExclusionStatus): ListStatus = ListStatus(
        parseIso(status.updated_at),
        listOf(getString(R.string.stat_entries) to status.entry_count)
    )

    // -------------------------------------------------------------- exclude

    private fun buildExclude(body: LinearLayout) {
        label(body, R.string.section_lists)
        card(body).apply {
            for (a in Account.values()) {
                addView(switchRow(getString(a.labelRes), getString(R.string.exclude_list_hint), excluded(a)) {
                    setExcluded(a, it)
                })
            }
        }
        label(body, R.string.section_planned)
        card(body).addView(switchRow(getString(R.string.keep_planned), getString(R.string.keep_planned_hint),
            FilterState.keepPlanned) { FilterState.keepPlanned = it })
    }

    private fun excluded(a: Account): Boolean = when (a) {
        Account.ANILIST -> FilterState.excludeAnilist
        Account.MAL -> FilterState.excludeMal
        Account.KITSU -> FilterState.excludeKitsu
        Account.YAMTRACK -> FilterState.excludeYamtrack
        Account.HARDCOVER -> FilterState.excludeHardcover
    }

    private fun setExcluded(a: Account, on: Boolean) {
        when (a) {
            Account.ANILIST -> FilterState.excludeAnilist = on
            Account.MAL -> FilterState.excludeMal = on
            Account.KITSU -> FilterState.excludeKitsu = on
            Account.YAMTRACK -> FilterState.excludeYamtrack = on
            Account.HARDCOVER -> FilterState.excludeHardcover = on
        }
        FilterState.save(requireContext())
    }

    // -------------------------------------------------------------- for you

    private fun buildForYou(body: LinearLayout) {
        label(body, R.string.row_for_you)
        val card = card(body)
        val stepperRow = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = dp(64)
            setPadding(dp(16), dp(12), dp(14), dp(12))
        }
        stepperRow.addView(textBlock(getString(R.string.seed_titles), getString(R.string.seed_titles_hint)))
        val value = text(15f, R.color.text, bold = true).apply {
            typeface = mono()
            gravity = Gravity.CENTER
            text = ForYouPrefs.seeds.toString()
            layoutParams = LinearLayout.LayoutParams(dp(30), WRAP)
        }
        val stepper = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = rounded(R.color.field, 999f, stroke = R.color.border_2)
        }
        stepper.addView(iconButton(R.drawable.mdi_minus) {
            ForYouPrefs.seeds = (ForYouPrefs.seeds - 1).coerceAtLeast(1)
            value.text = ForYouPrefs.seeds.toString()
        })
        stepper.addView(value)
        stepper.addView(iconButton(R.drawable.mdi_plus) {
            ForYouPrefs.seeds = (ForYouPrefs.seeds + 1).coerceAtMost(20)
            value.text = ForYouPrefs.seeds.toString()
        })
        stepperRow.addView(stepper)
        card.addView(stepperRow)
        card.addView(switchRow(getString(R.string.weight_by_my_ratings), getString(R.string.ratings_hint),
            ForYouPrefs.useRatings) { ForYouPrefs.useRatings = it })
    }

    // -------------------------------------------------------------- display

    private fun buildDisplay(body: LinearLayout) {
        label(body, R.string.row_display)
        val card = card(body)
        val inner = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(14), dp(14), dp(14))
        }
        inner.addView(text(14.5f, R.color.text).apply {
            text = getString(R.string.title_language)
            setPadding(0, 0, 0, dp(10))
        })
        val segments = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            background = rounded(R.color.field, 12f, stroke = R.color.border_2)
            setPadding(dp(3), dp(3), dp(3), dp(3))
        }
        for ((labelRes, romaji) in listOf(R.string.english to false, R.string.romaji to true)) {
            val selected = FilterState.romajiTitles == romaji
            segments.addView(text(13.5f, if (selected) R.color.text else R.color.muted, bold = selected).apply {
                text = getString(labelRes)
                gravity = Gravity.CENTER
                minHeight = dp(44)
                if (selected) background = rounded(R.color.border_2, 9f)
                layoutParams = LinearLayout.LayoutParams(0, dp(44), 1f)
                isClickable = true
                setOnClickListener {
                    FilterState.romajiTitles = romaji
                    FilterState.save(requireContext())
                    (activity as? MainActivity)?.discoverFragment()?.refreshTitles()
                    render()
                }
            })
        }
        inner.addView(segments)
        inner.addView(text(12f, R.color.dim).apply {
            text = getString(R.string.title_language_hint)
            setPadding(0, dp(8), 0, 0)
        })
        card.addView(inner)
    }

    // --------------------------------------------------------------- server

    /** How to reach the server. Local to this device, unlike everything else. */
    private fun buildServer(body: LinearLayout) {
        val prefs = Prefs(requireContext())
        label(body, R.string.section_server)
        val fields = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(MATCH, WRAP).apply { setMargins(dp(14), 0, dp(14), dp(16)) }
        }
        body.addView(fields)
        val url = field(fields, R.string.server_url,
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        url.hint = getString(R.string.server_url_hint)
        url.setText(prefs.serverUrl?.trimEnd('/').orEmpty())
        val token = field(fields, R.string.access_token,
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
        token.setText(prefs.token.orEmpty())
        fields.addView(text(12f, R.color.dim).apply {
            text = getString(R.string.access_token_help)
            setPadding(dp(4), dp(6), dp(4), dp(12))
        })
        val status = text(13f, R.color.muted).apply { setPadding(dp(4), dp(10), dp(4), 0) }
        fields.addView(pill(getString(R.string.save)).apply {
            setOnClickListener {
                val value = url.text.toString().trim()
                when {
                    value.isEmpty() -> status.text = getString(R.string.error_url_required)
                    !value.startsWith("http://") && !value.startsWith("https://") ->
                        status.text = getString(R.string.error_url_scheme)
                    else -> {
                        prefs.serverUrl = value
                        // Blank clears it, the normal state for a LAN server.
                        prefs.token = token.text.toString().trim().ifBlank { null }
                        ServiceLocator.invalidate()
                        status.text = getString(R.string.checking)
                        verify(status)
                    }
                }
            }
        })
        fields.addView(status)

        // "Access token · Saved on this device" with Forget (3b).
        val saved = !prefs.token.isNullOrBlank()
        val row = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = dp(64)
            setPadding(dp(14), dp(12), dp(14), dp(12))
        }
        row.addView(ImageView(requireContext()).apply {
            setImageResource(R.drawable.mdi_key_variant)
            setColorFilter(color(R.color.muted))
            layoutParams = LinearLayout.LayoutParams(dp(18), dp(18)).apply { marginEnd = dp(8) }
        })
        row.addView(textBlock(getString(R.string.access_token).substringBefore(" ("),
            getString(if (saved) R.string.token_on_device else R.string.token_not_saved)))
        if (saved) {
            row.addView(text(13.5f, R.color.bad).apply {
                text = getString(R.string.forget)
                minHeight = dp(44)
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(12), 0, dp(4), 0)
                isClickable = true
                setOnClickListener {
                    prefs.token = null
                    ServiceLocator.invalidate()
                    toast(getString(R.string.token_forgotten))
                    render()
                }
            })
        }
        card(body).addView(row)

        buildTmdbKey(body)
    }

    /**
     * The TMDB key Films and TV are fetched with. Stored on the server, not
     * this device: the worker does the fetching. Write-only, like the
     * tracker tokens - the reply only says whether one is saved.
     */
    private fun buildTmdbKey(body: LinearLayout) {
        label(body, R.string.section_tmdb)
        val fields = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(MATCH, WRAP).apply { setMargins(dp(14), 0, dp(14), dp(16)) }
        }
        body.addView(fields)
        val key = field(fields, R.string.tmdb_api_key,
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
        key.hint = getString(if (tmdbKeySet) R.string.saved else R.string.tmdb_api_key_hint)
        fields.addView(text(12f, R.color.dim).apply {
            text = getString(R.string.tmdb_attribution)
            setPadding(dp(4), dp(6), dp(4), dp(12))
        })
        fields.addView(pill(getString(R.string.save)).apply {
            setOnClickListener {
                val value = key.text.toString().trim()
                if (value.isEmpty()) {
                    toast(getString(R.string.needs_token))
                    return@setOnClickListener
                }
                val api = api() ?: return@setOnClickListener
                viewLifecycleOwner.lifecycleScope.launch {
                    try {
                        readUsernames(api.saveSettings(AppSettingsIn(tmdb_api_key = value)))
                        key.setText("")
                        key.hint = getString(R.string.saved)
                        toast(getString(R.string.saved))
                    } catch (e: Exception) {
                        toast(message(e))
                    }
                }
            }
        })
    }

    /**
     * Saving is not the same as working, so this proves the server answers
     * before the reader walks away. A wrong URL, a server that is down and
     * one that wants a token all look identical from the search screen.
     */
    private fun verify(status: TextView) {
        viewLifecycleOwner.lifecycleScope.launch {
            val api = ServiceLocator.api(requireContext())
            if (api == null) {
                status.text = getString(R.string.error_url_required)
                return@launch
            }
            try {
                val response = api.search(null, mapOf("limit" to "1"))
                status.text = getString(R.string.connected, response.results.size)
                settingsLoaded = false
                statusesLoaded = false
                loadSettings()
            } catch (e: retrofit2.HttpException) {
                status.text = when (e.code()) {
                    401 -> getString(R.string.error_token)
                    404 -> getString(R.string.error_not_mangabrain)
                    else -> httpMessage(requireContext(), e)
                }
            } catch (e: Exception) {
                status.text = getString(R.string.error_unreachable, e.message ?: "")
            }
        }
    }

    // ---------------------------------------------------------------- media
    //
    // Nothing here names a medium. The datasets, the media each fills and
    // what installing costs all come from GET /datasets, so a medium added
    // on the server appears here without an app update. Only the tab labels
    // are local, and an unknown key falls back to showing the key.

    private fun buildMedia(body: LinearLayout) {
        body.addView(text(13f, R.color.muted).apply {
            text = getString(R.string.media_intro)
            setLineSpacing(0f, 1.15f)
            setPadding(dp(22), dp(4), dp(22), dp(18))
        })
        val container = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(MATCH, WRAP).apply { setMargins(dp(14), 0, dp(14), 0) }
        }
        body.addView(container)
        loadMedia(container)
    }

    /** Re-polls while an install is under way and this page is on screen. */
    private fun loadMedia(container: LinearLayout) {
        pollJob?.cancel()
        val api = api() ?: return
        pollJob = viewLifecycleOwner.lifecycleScope.launch {
            while (isActive) {
                val active = try {
                    FilterState.applySettings(api.settings())
                    datasets = api.datasets().datasets
                    renderMedia(container, datasets)
                    datasets.any { it.install?.state in ACTIVE }
                } catch (e: Exception) {
                    toast(message(e))
                    false
                }
                if (!active || isHidden || stack.last() != Page.MEDIA) break
                delay(4000)
            }
        }
    }

    private fun renderMedia(container: LinearLayout, items: List<DatasetOut>) {
        container.removeAllViews()
        val enabled = FilterState.visibleMedia()
        for ((index, dataset) in items.withIndex()) {
            val install = dataset.install
            val card = LinearLayout(requireContext()).apply {
                orientation = LinearLayout.VERTICAL
                background = ContextCompat.getDrawable(context, R.drawable.bg_card)
                layoutParams = LinearLayout.LayoutParams(MATCH, WRAP).apply {
                    if (index > 0) topMargin = dp(12)
                }
            }
            val head = LinearLayout(requireContext()).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(16), dp(14), dp(16), dp(10))
            }
            head.addView(text(16f, R.color.text, bold = true).apply { text = dataset.label })
            val statusColor = when {
                install?.state == "failed" -> R.color.bad
                install?.state in ACTIVE -> R.color.accent_text
                else -> R.color.muted
            }
            head.addView(text(12.5f, statusColor).apply {
                text = installText(dataset)
                setLineSpacing(0f, 1.15f)
                setPadding(0, dp(3), 0, 0)
            })
            val total = install?.total ?: 0L
            if (install != null && install.state == "running" && install.phase == "downloading" && total > 0) {
                val done = install.bytes ?: 0L
                head.addView(ProgressBar(requireContext(), null, android.R.attr.progressBarStyleHorizontal).apply {
                    progressDrawable = ContextCompat.getDrawable(context, R.drawable.progress_install)
                    max = 1000
                    progress = (done * 1000 / total).toInt()
                    layoutParams = LinearLayout.LayoutParams(MATCH, dp(6)).apply { topMargin = dp(10) }
                })
            }
            card.addView(head)
            for (group in dataset.media) {
                card.addView(divider())
                val row = switchRow(FilterState.MEDIUM_LABELS[group] ?: group, null, group in enabled, null)
                val switch = row.findViewWithTag<MaterialSwitch>(SWITCH_TAG)
                // A click, not a checked-change listener: reverting the
                // switch after a cancelled download must not count as the
                // reader switching it off.
                switch.setOnClickListener { toggleMedium(group, switch.isChecked, dataset, switch, container) }
                row.setOnClickListener {
                    switch.isChecked = !switch.isChecked
                    toggleMedium(group, switch.isChecked, dataset, switch, container)
                }
                card.addView(row)
            }
            val busy = install?.state in ACTIVE
            if (dataset.installable && !busy && (install?.state == "failed" || dataset.installed)) {
                card.addView(divider())
                card.addView(text(14f, R.color.accent_text).apply {
                    text = getString(if (install?.state == "failed") R.string.media_retry else R.string.media_update)
                    gravity = Gravity.CENTER_VERTICAL
                    minHeight = dp(52)
                    setPadding(dp(16), 0, dp(16), 0)
                    isClickable = true
                    setOnClickListener { requestInstall(dataset, container) }
                })
            }
            container.addView(card)
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

    private fun toggleMedium(
        group: String,
        on: Boolean,
        dataset: DatasetOut,
        switch: MaterialSwitch,
        container: LinearLayout
    ) {
        val current = FilterState.visibleMedia()
        val next = if (on) (current + group).distinct() else current - group
        if (next.isEmpty()) {
            switch.isChecked = true
            toast(getString(R.string.media_keep_one))
            return
        }
        val willDownload = on && dataset.installable && !dataset.installed &&
            dataset.install?.state !in ACTIVE
        if (!willDownload) {
            saveMedia(next, switch, on, container)
            return
        }
        // Asked before, never after: switching this on is what starts it.
        AlertDialog.Builder(requireContext())
            .setTitle(getString(R.string.media_download_title, dataset.label))
            .setMessage(dataset.download_hint)
            .setPositiveButton(R.string.media_download) { _, _ -> saveMedia(next, switch, on, container) }
            .setNegativeButton(android.R.string.cancel) { _, _ -> switch.isChecked = false }
            .setOnCancelListener { switch.isChecked = false }
            .show()
    }

    private fun saveMedia(next: List<String>, switch: MaterialSwitch, on: Boolean, container: LinearLayout) {
        val api = api() ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                FilterState.applySettings(api.saveSettings(AppSettingsIn(enabled_media = next)))
            } catch (e: Exception) {
                switch.isChecked = !on
                toast(message(e))
            }
            loadMedia(container)
        }
    }

    private fun requestInstall(dataset: DatasetOut, container: LinearLayout) {
        val api = api() ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                api.installDataset(dataset.key)
            } catch (e: Exception) {
                toast(message(e))
            }
            loadMedia(container)
        }
    }

    private fun loadDatasetsForSummary() {
        val api = api(quiet = true) ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            val fresh = try {
                api.datasets().datasets
            } catch (e: Exception) {
                return@launch
            }
            val changed = fresh != datasets
            datasets = fresh
            if (changed && stack.last() == Page.HOME && !isHidden) render()
        }
    }

    // ------------------------------------------------------------- settings

    private fun loadSettings() {
        val api = api(quiet = true) ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val settings = api.settings()
                FilterState.applySettings(settings)
                readUsernames(settings)
                settingsLoaded = true
                statusesLoaded = false
                if (!isHidden) render()
            } catch (e: Exception) {
                // The home page still works from what is known; the error
                // surfaces on the page that needs the server.
            }
        }
    }

    private fun readUsernames(settings: com.mangabrain.app.data.api.AppSettingsOut) {
        usernames[Account.ANILIST] = settings.anilist_username.orEmpty()
        usernames[Account.MAL] = settings.mal_username.orEmpty()
        usernames[Account.KITSU] = settings.kitsu_username.orEmpty()
        usernames[Account.YAMTRACK] = settings.yamtrack_url.orEmpty()
        yamtrackTokenSet = settings.yamtrack_token_set == true
        hardcoverTokenSet = settings.hardcover_token_set == true
        tmdbKeySet = settings.tmdb_api_key_set == true
    }

    private fun connected(a: Account): Boolean =
        if (a == Account.HARDCOVER) hardcoverTokenSet else !usernames[a].isNullOrBlank()

    private fun displayName(a: Account): String = when (a) {
        Account.YAMTRACK -> usernames[a].orEmpty().removePrefix("https://").removePrefix("http://")
        Account.HARDCOVER -> getString(R.string.hardcover_token_saved)
        else -> usernames[a].orEmpty()
    }

    // ------------------------------------------------------------- building

    private fun label(body: ViewGroup, res: Int) {
        body.addView(TextView(requireContext()).apply {
            text = getString(res)
            isAllCaps = true
            letterSpacing = 0.11f
            textSize = 10.5f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(R.color.muted))
            setPadding(dp(22), dp(6), dp(22), dp(8))
        })
    }

    /** Surface, 1dp border, 18dp radius, 14dp inset, 1dp lines between rows. */
    private fun card(body: ViewGroup): LinearLayout = LinearLayout(requireContext()).apply {
        orientation = LinearLayout.VERTICAL
        background = ContextCompat.getDrawable(context, R.drawable.bg_card)
        dividerDrawable = ContextCompat.getDrawable(context, R.drawable.divider)
        showDividers = LinearLayout.SHOW_DIVIDER_MIDDLE
        clipToOutline = true
        layoutParams = LinearLayout.LayoutParams(MATCH, WRAP).apply { setMargins(dp(14), 0, dp(14), dp(16)) }
        body.addView(this)
    }

    /** A home row: 36dp icon tile, title, one-line summary, chevron. */
    private fun navRow(icon: Int, tint: Int, title: String, summary: String, onClick: () -> Unit): View {
        val tile = FrameLayout(requireContext()).apply {
            background = ContextCompat.getDrawable(context, R.drawable.bg_row_tile)
            addView(ImageView(context).apply {
                setImageResource(icon)
                setColorFilter(color(tint))
                layoutParams = FrameLayout.LayoutParams(dp(20), dp(20), Gravity.CENTER)
            })
        }
        return row(tile, title, summary, onClick)
    }

    private fun row(leading: View, title: String, summary: String, onClick: () -> Unit): View =
        LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = dp(64)
            setPadding(dp(16), dp(10), dp(16), dp(10))
            background = selectable()
            isClickable = true
            setOnClickListener { onClick() }
            addView(leading.apply {
                layoutParams = LinearLayout.LayoutParams(dp(36), dp(36)).apply { marginEnd = dp(14) }
            })
            addView(textBlock(title, summary, titleSize = 15f))
            addView(ImageView(context).apply {
                setImageResource(R.drawable.mdi_chevron_right)
                setColorFilter(color(R.color.nav_handle))
                layoutParams = LinearLayout.LayoutParams(dp(22), dp(22))
            })
        }

    /** A title and a hint, left; a switch, right. The whole row toggles. */
    private fun switchRow(title: String, hint: String?, checked: Boolean, onChange: ((Boolean) -> Unit)?): View {
        val switch = MaterialSwitch(requireContext()).apply {
            isChecked = checked
            tag = SWITCH_TAG
            if (onChange != null) setOnCheckedChangeListener { _, on -> onChange(on) }
        }
        return LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = dp(56)
            setPadding(dp(16), dp(10), dp(12), dp(10))
            background = selectable()
            isClickable = true
            if (onChange != null) setOnClickListener { switch.isChecked = !switch.isChecked }
            addView(textBlock(title, hint))
            addView(switch)
        }
    }

    private fun textBlock(title: String, hint: String?, titleSize: Float = 14.5f): View =
        LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, WRAP, 1f)
            addView(text(titleSize, R.color.text).apply { text = title })
            if (!hint.isNullOrEmpty()) {
                addView(text(12.5f, R.color.muted).apply {
                    text = hint
                    setPadding(0, dp(2), 0, 0)
                })
            }
        }

    /** A labelled 52dp field with 16sp text (3b page 2). */
    private fun field(parent: LinearLayout, labelRes: Int, type: Int): EditText {
        parent.addView(text(12f, R.color.muted).apply {
            text = getString(labelRes)
            setPadding(dp(4), dp(6), dp(4), dp(6))
        })
        return EditText(requireContext()).apply {
            inputType = type
            background = ContextCompat.getDrawable(context, R.drawable.bg_field)
            setTextColor(color(R.color.text))
            setHintTextColor(color(R.color.dim))
            textSize = 16f
            // No setSingleLine(): it swaps the transformation method, which
            // would show a password field's text. A text input type without
            // the multi-line flag is single-line already.
            setPadding(dp(16), 0, dp(16), 0)
            layoutParams = LinearLayout.LayoutParams(MATCH, dp(52))
            parent.addView(this)
        }
    }

    private fun text(size: Float, colorRes: Int, bold: Boolean = false): TextView =
        TextView(requireContext()).apply {
            textSize = size
            setTextColor(color(colorRes))
            if (bold) setTypeface(typeface, Typeface.BOLD)
        }

    private fun pill(label: String, bad: Boolean = false, compact: Boolean = false): MaterialButton {
        val style = if (bad) com.google.android.material.R.attr.materialButtonOutlinedStyle
        else com.google.android.material.R.attr.materialButtonStyle
        return MaterialButton(requireContext(), null, style).apply {
            text = label
            isAllCaps = false
            textSize = if (compact) 13.5f else 14.5f
            setTypeface(typeface, Typeface.BOLD)
            cornerRadius = dp(999)
            insetTop = 0
            insetBottom = 0
            minHeight = dp(if (compact) 40 else 52)
            minimumHeight = dp(if (compact) 40 else 52)
            if (bad) {
                setTextColor(color(R.color.bad))
                strokeColor = ContextCompat.getColorStateList(context, R.color.bad_bg)
            } else {
                backgroundTintList = ContextCompat.getColorStateList(context, R.color.accent)
                setTextColor(color(android.R.color.white))
            }
        }
    }

    private fun iconButton(icon: Int, onClick: () -> Unit): View = ImageButton(requireContext()).apply {
        setImageResource(icon)
        setColorFilter(color(R.color.text_2))
        background = selectable()
        layoutParams = LinearLayout.LayoutParams(dp(44), dp(44))
        setOnClickListener { onClick() }
    }

    private fun accountBadge(
        a: Account,
        sizeDp: Int,
        circle: Boolean,
        radius: Float = 11f
    ): ImageView = ImageView(requireContext()).apply {
        setImageResource(a.logoRes)
        contentDescription = getString(a.labelRes)
        background = GradientDrawable().apply {
            if (circle) {
                shape = GradientDrawable.OVAL
                setStroke(dp(2), color(R.color.sync_from))
            } else {
                cornerRadius = dp(radius).toFloat()
            }
            setColor(color(a.bgRes))
        }
        // The background's shape is the outline, so the Yamtrack icon is
        // cut to the same circle or rounded square as the other three.
        clipToOutline = true
        if (a.tinted) {
            scaleType = ImageView.ScaleType.FIT_CENTER
            val pad = dp(sizeDp) * 22 / 100
            setPadding(pad, pad, pad, pad)
        } else {
            scaleType = ImageView.ScaleType.CENTER_CROP
        }
        layoutParams = LinearLayout.LayoutParams(dp(sizeDp), dp(sizeDp))
    }

    private fun divider(): View = View(requireContext()).apply {
        setBackgroundColor(color(R.color.border))
        layoutParams = LinearLayout.LayoutParams(MATCH, dp(1))
    }

    private fun rounded(fill: Int, radiusDp: Float, stroke: Int? = null) = GradientDrawable().apply {
        setColor(color(fill))
        cornerRadius = dp(radiusDp).toFloat()
        if (stroke != null) setStroke(dp(1), color(stroke))
    }

    private fun selectable() = with(android.util.TypedValue()) {
        requireContext().theme.resolveAttribute(android.R.attr.selectableItemBackground, this, true)
        ContextCompat.getDrawable(requireContext(), resourceId)
    }

    private fun mono(): Typeface? = ResourcesCompat.getFont(requireContext(), R.font.jetbrains_mono)

    // -------------------------------------------------------------- helpers

    private fun api(quiet: Boolean = false): MangaBrainApi? {
        val api = ServiceLocator.api(requireContext())
        if (api == null && !quiet) toast(getString(R.string.needs_server))
        return api
    }

    /**
     * Server-supplied detail wins over a generic message: a refresh failure
     * usually carries the only useful explanation ("MAL user not found").
     */
    private fun message(e: Exception): String = when (e) {
        is retrofit2.HttpException ->
            if (e.code() == 401) getString(R.string.error_token) else httpMessage(requireContext(), e)
        else -> getString(R.string.error_unreachable, e.message ?: "")
    }

    private fun toast(text: String) {
        context?.let { Toast.makeText(it, text, Toast.LENGTH_SHORT).show() }
    }

    private fun color(res: Int): Int = ContextCompat.getColor(requireContext(), res)

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun dp(value: Float): Int = (value * resources.displayMetrics.density).toInt()

    private fun gb(bytes: Long): String = String.format("%.2f GB", bytes / 1e9)

    private fun timeAgo(millis: Long): String {
        val minutes = ((System.currentTimeMillis() - millis) / 60_000).coerceAtLeast(0)
        return when {
            minutes < 1 -> getString(R.string.just_now)
            minutes < 60 -> resources.getQuantityString(R.plurals.minutes_ago, minutes.toInt(), minutes.toInt())
            minutes < 60 * 24 -> (minutes / 60).toInt().let {
                resources.getQuantityString(R.plurals.hours_ago, it, it)
            }
            else -> (minutes / (60 * 24)).toInt().let {
                resources.getQuantityString(R.plurals.days_ago, it, it)
            }
        }
    }

    companion object {
        private val ACTIVE = setOf("queued", "running", "waiting")
        private const val SWITCH_TAG = "switch"
        private val MATCH = ViewGroup.LayoutParams.MATCH_PARENT
        private val WRAP = ViewGroup.LayoutParams.WRAP_CONTENT

        private val ISO = Regex(
            """^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"""
        )

        /**
         * The server's timestamps, without java.time: minSdk is 24 and
         * java.time arrived in 26. Handles a trailing Z, an offset, or none
         * (taken as UTC, which is what the server writes). Null when the
         * string is not one of those, which shows as "not synced" rather
         * than as a wrong time.
         */
        internal fun parseIso(value: String?): Long? {
            val m = ISO.matchEntire(value?.trim() ?: return null) ?: return null
            val (y, mo, d, h, mi, s, zone) = m.destructured
            val cal = Calendar.getInstance(TimeZone.getTimeZone("UTC")).apply {
                clear()
                set(y.toInt(), mo.toInt() - 1, d.toInt(), h.toInt(), mi.toInt(), s.toInt())
            }
            var millis = cal.timeInMillis
            if (zone.isNotEmpty() && zone != "Z") {
                val sign = if (zone[0] == '-') -1 else 1
                val digits = zone.substring(1).replace(":", "")
                val offset = digits.substring(0, 2).toInt() * 60 + digits.substring(2, 4).toInt()
                millis -= sign * offset * 60_000L
            }
            return millis
        }
    }
}
