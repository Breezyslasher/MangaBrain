import SwiftUI

/// The Settings tab: a short home page of rows with live summaries, and a
/// page behind each, as on Android.
struct SettingsView: View {
    @Environment(AppModel.self) private var app
    @Environment(Filters.self) private var filters
    @State private var model = SettingsModel()
    @State private var error: String?

    var body: some View {
        @Bindable var app = app
        NavigationStack(path: $app.settingsPath) {
            List {
                Section { syncCard }
                Section("Lists") {
                    row(.profile, "Profile", "person.2", Theme.wTaste, "\(app.profile) · its own accounts and lists")
                    row(.accounts, "Accounts", "person.crop.circle", Theme.accent, accountsSummary)
                    row(.exclude, "Exclude from results", "eye.slash", Theme.bad, excludeSummary)
                }
                Section("Recommendations") {
                    row(.forYou, "For you", "sparkles", Theme.ok,
                        "\(app.forYouSeeds) seeds · \(app.forYouRatings ? "weighted by ratings" : "unweighted")")
                    row(.media, "Media", "books.vertical", Theme.wGenres, mediaSummary)
                }
                Section("App") {
                    row(.display, "Display", "character.bubble", Theme.muted,
                        filters.romajiTitles ? "Romaji or original titles" : "Titles in English")
                    row(.server, "Server", "key", Theme.muted, serverSummary)
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.bg)
            .navigationTitle("Settings")
            .navigationDestination(for: SettingsPage.self) { page in
                destination(page)
                    .scrollContentBackground(.hidden)
                    .background(Theme.bg)
            }
            .refreshable { await model.load(app) }
            .alert("Something went wrong", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(error ?? "")
            }
        }
        .environment(model)
        .task(id: app.generation) {
            model.reset()
            await model.load(app)
        }
    }

    @ViewBuilder
    private func destination(_ page: SettingsPage) -> some View {
        switch page {
        case .profile: ProfilePage()
        case .accounts: AccountsPage()
        case .account(let account): AccountPage(account: account)
        case .exclude: ExcludePage()
        case .forYou: ForYouPage()
        case .media: MediaPage()
        case .display: DisplayPage()
        case .server: ServerPage()
        }
    }

    private func row(_ page: SettingsPage, _ title: String, _ icon: String, _ tint: Color, _ summary: String) -> some View {
        NavigationLink(value: page) {
            HStack(spacing: 14) {
                Image(systemName: icon)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(tint)
                    .frame(width: 34, height: 34)
                    .background(tint.opacity(0.15), in: RoundedRectangle(cornerRadius: 9))
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).foregroundStyle(Theme.text)
                    Text(summary).font(.caption).foregroundStyle(Theme.muted).lineLimit(2)
                }
            }
            .padding(.vertical, 2)
        }
    }

    /// "2 of 5 lists connected", last sync, and Sync all.
    private var syncCard: some View {
        let connected = model.connectedAccounts
        return HStack(spacing: 12) {
            HStack(spacing: -8) {
                ForEach(connected) { AccountBadge(account: $0, size: 34, circle: true) }
            }
            VStack(alignment: .leading, spacing: 2) {
                Text("\(connected.count) of \(Account.allCases.count) lists connected")
                    .font(.subheadline.weight(.bold))
                Text(connected.isEmpty ? "Connect a list to hide what you have seen"
                     : model.latestSync.map { "Last sync \(timeAgo($0))" } ?? "Not synced yet")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
            Spacer()
            Button(model.syncing.isEmpty ? "Sync all" : "Syncing…") {
                Task {
                    for account in connected {
                        do { try await model.sync(account, app: app) } catch { self.error = error.localizedDescription }
                    }
                }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .disabled(connected.isEmpty || !model.syncing.isEmpty)
        }
        .padding(.vertical, 4)
    }

    private var accountsSummary: String {
        let names = model.connectedAccounts.map(\.label)
        return names.isEmpty ? "No accounts connected" : "\(names.joined(separator: ", ")) connected"
    }

    private var excludeSummary: String {
        let names = Account.allCases.filter { filters.excluded.contains($0) }.map(\.label)
        let lists = names.isEmpty ? "No lists excluded" : names.joined(separator: ", ")
        return "\(lists) · keep planned \(filters.keepPlanned ? "on" : "off")"
    }

    private var mediaSummary: String {
        let tabs = "\(filters.visibleMedia.count) tabs"
        guard let busy = model.datasets.first(where: \.busy), let install = busy.install else { return tabs }
        let total = install.total ?? 0
        let percent = total > 0 ? Int((install.bytes ?? 0) * 100 / total) : 0
        switch (install.state, install.phase) {
        case ("running", "downloading") where total > 0: return "\(tabs) · \(busy.label) downloading \(percent)%"
        case ("running", "fetching") where total > 0: return "\(tabs) · \(busy.label) fetching \(percent)%"
        case ("running", _): return "\(tabs) · \(busy.label) installing"
        case ("queued", _): return "\(tabs) · \(busy.label) queued"
        default: return "\(tabs) · \(busy.label) waiting"
        }
    }

    private var serverSummary: String {
        if !app.isConfigured { return "Server not set up" }
        return app.token.isEmpty ? "No access token" : "Access token saved"
    }
}

// MARK: - Shared pieces

struct AccountBadge: View {
    let account: Account
    var size: CGFloat = 36
    var circle = false

    var body: some View {
        Text(account.short)
            .font(.system(size: size * (account.short.count > 2 ? 0.3 : 0.4), weight: .heavy))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(account.color, in: RoundedRectangle(cornerRadius: circle ? size / 2 : size * 0.28))
            .overlay(RoundedRectangle(cornerRadius: circle ? size / 2 : size * 0.28).stroke(Theme.bg, lineWidth: circle ? 2 : 0))
    }
}

// MARK: - Profile

/// Profiles on the server: each has its own account names, tokens and
/// synced lists. This device uses one.
struct ProfilePage: View {
    @Environment(AppModel.self) private var app
    @State private var names: [String] = []
    @State private var message: String?
    @State private var asking = false
    @State private var newName = ""
    @State private var confirmRemove = false

    var body: some View {
        List {
            Section {
                ForEach(names.isEmpty ? [app.profile] : names, id: \.self) { name in
                    Button {
                        if name != app.profile { app.useProfile(name) }
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(name).foregroundStyle(Theme.text)
                                Text(name == app.profile ? "Used on this device" : "Tap to use")
                                    .font(.caption).foregroundStyle(Theme.muted)
                            }
                            Spacer()
                            if name == app.profile { Image(systemName: "checkmark").foregroundStyle(Theme.ok) }
                        }
                    }
                }
            } header: {
                Text("Profiles")
            } footer: {
                Text(message ?? "Each profile has its own account names, tokens and synced lists. Filters are remembered per device.")
            }
            Section {
                Button("New profile") { newName = ""; asking = true }
                if app.profile != AppModel.defaultProfile {
                    Button("Remove this profile", role: .destructive) { confirmRemove = true }
                }
            }
        }
        .navigationTitle("Profile")
        .task(id: app.generation) { await load() }
        .alert("New profile", isPresented: $asking) {
            TextField("a-z, 0-9, - and _", text: $newName)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            Button("Cancel", role: .cancel) {}
            Button("Save") { Task { await create() } }
        }
        .confirmationDialog("Remove the profile \"\(app.profile)\" with its accounts and lists?",
                            isPresented: $confirmRemove, titleVisibility: .visible) {
            Button("Remove this profile", role: .destructive) { Task { await remove() } }
        }
    }

    private func load() async {
        guard let client = app.client else { return }
        do {
            names = try await client.profiles().profiles ?? []
            message = nil
        } catch {
            message = error.localizedDescription
        }
    }

    private func create() async {
        let name = newName.trimmed.lowercased()
        guard !name.isEmpty, let client = app.client else { return }
        do {
            _ = try await client.makeProfile(name)
            app.useProfile(name)
        } catch {
            message = error.localizedDescription
        }
    }

    private func remove() async {
        guard let client = app.client else { return }
        do {
            _ = try await client.removeProfile(app.profile)
            app.useProfile(AppModel.defaultProfile)
        } catch {
            message = error.localizedDescription
        }
    }
}

// MARK: - Accounts

struct AccountsPage: View {
    @Environment(SettingsModel.self) private var model

    var body: some View {
        List {
            ForEach(Account.allCases) { account in
                NavigationLink(value: SettingsPage.account(account)) {
                    HStack(spacing: 14) {
                        AccountBadge(account: account)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(account.label)
                            Text(summary(account)).font(.caption).foregroundStyle(Theme.muted)
                        }
                    }
                }
            }
        }
        .navigationTitle("Accounts")
    }

    private func summary(_ account: Account) -> String {
        guard model.connected(account) else { return "Not connected" }
        var parts: [String] = []
        if [.anilist, .mal, .kitsu].contains(account) { parts.append(model.username(account)) }
        if let synced = model.statuses[account]?.syncedAt { parts.append("Synced \(timeAgo(synced))") }
        return parts.isEmpty ? model.displayName(account) : parts.joined(separator: " · ")
    }
}

/// One template for every list source.
struct AccountPage: View {
    @Environment(AppModel.self) private var app
    @Environment(Filters.self) private var filters
    @Environment(SettingsModel.self) private var model
    let account: Account

    @State private var primary = ""
    @State private var secret = ""
    @State private var message: String?
    @State private var busy = false

    var body: some View {
        List {
            Section {
                VStack(spacing: 8) {
                    AccountBadge(account: account, size: 68)
                    Text(model.connected(account) ? model.displayName(account) : "Not connected")
                        .font(.title3.weight(.bold))
                    Text(statusLine)
                        .font(.footnote)
                        .foregroundStyle(statusColor)
                        .multilineTextAlignment(.center)
                    if let tiles = model.statuses[account]?.tiles, !tiles.isEmpty {
                        HStack(spacing: 8) {
                            ForEach(tiles, id: \.self) { tile in
                                VStack(spacing: 3) {
                                    Text(tile.count.formatted()).font(.title3.monospacedDigit().weight(.bold))
                                    Text(tile.name).font(.caption).foregroundStyle(Theme.muted)
                                }
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 10)
                                .background(Theme.card, in: RoundedRectangle(cornerRadius: 12))
                            }
                        }
                        .padding(.top, 6)
                    }
                }
                .frame(maxWidth: .infinity)
                .listRowBackground(Color.clear)
            }
            Section("Account") {
                switch account {
                case .yamtrack:
                    TextField("http://host:port", text: $primary)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField(model.settings?.yamtrackTokenSet == true ? "Saved" : "Token from your Yamtrack profile",
                                text: $secret)
                case .hardcover:
                    SecureField(model.connected(account) ? "Saved" : "Token from hardcover.app, Settings, API",
                                text: $primary)
                default:
                    TextField("Username", text: $primary)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                if model.connected(account) {
                    Button("Save changes") { Task { await saveChanges() } }
                        .disabled(busy)
                }
            }
            Section {
                Toggle(isOn: Binding(
                    get: { filters.excluded.contains(account) },
                    set: { on in
                        if on { filters.excluded.insert(account) } else { filters.excluded.remove(account) }
                        filters.save()
                    }
                )) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Exclude this list")
                        Text("Also feeds My taste and For you").font(.caption).foregroundStyle(Theme.muted)
                    }
                }
            } header: {
                Text("In results")
            }
            Section {
                if model.connected(account) {
                    Button("Sync now") { Task { await sync() } }
                        .disabled(busy)
                    Button("Disconnect", role: .destructive) { Task { await disconnect() } }
                        .disabled(busy)
                } else {
                    Button("Connect") { Task { await connect() } }
                        .disabled(busy)
                }
            }
        }
        .navigationTitle(account.label)
        .onAppear {
            primary = account == .hardcover ? "" : model.username(account)
        }
    }

    private var statusLine: String {
        if let message { return message }
        if model.syncing.contains(account) { return "Syncing…" }
        if let synced = model.statuses[account]?.syncedAt { return "Synced \(timeAgo(synced))" }
        return "Not synced"
    }

    private var statusColor: Color {
        if message != nil { return Theme.bad }
        if model.syncing.contains(account) { return Theme.accentText }
        return model.statuses[account]?.syncedAt != nil ? Theme.ok : Theme.muted
    }

    private func run(_ work: () async throws -> Void) async {
        busy = true
        message = nil
        do { try await work() } catch { message = error.localizedDescription }
        busy = false
    }

    private func connect() async {
        let value = primary.trimmed
        guard !value.isEmpty else {
            message = account == .hardcover ? "Paste your API token first." : "Enter the username first."
            return
        }
        await run {
            try await model.save(account, value: value, yamtrackToken: secret.trimmed.isEmpty ? nil : secret.trimmed, app: app)
            secret = ""
            if account == .hardcover { primary = "" }
            try await model.sync(account, app: app)
        }
    }

    /// Tokens are write-only: an empty field means unchanged.
    private func saveChanges() async {
        let value = primary.trimmed
        let token = secret.trimmed
        await run {
            switch account {
            case .hardcover:
                guard !value.isEmpty else { return }
                try await model.save(account, value: value, app: app)
                primary = ""
            case .yamtrack:
                try await model.save(account, value: value.isEmpty ? nil : value,
                                     yamtrackToken: token.isEmpty ? nil : token, app: app)
                secret = ""
            default:
                guard !value.isEmpty, value != model.username(account) else { return }
                try await model.save(account, value: value, app: app)
            }
        }
    }

    private func sync() async {
        await run { try await model.sync(account, app: app) }
    }

    private func disconnect() async {
        await run {
            try await model.save(account, value: "", app: app)
            primary = ""
        }
    }
}

// MARK: - Exclude, For you, Display

struct ExcludePage: View {
    @Environment(Filters.self) private var filters

    var body: some View {
        @Bindable var filters = filters
        List {
            Section("Lists") {
                ForEach(Account.allCases) { account in
                    Toggle(isOn: Binding(
                        get: { filters.excluded.contains(account) },
                        set: { on in
                            if on { filters.excluded.insert(account) } else { filters.excluded.remove(account) }
                            filters.save()
                        }
                    )) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(account.label)
                            Text("Hide titles already on it from results").font(.caption).foregroundStyle(Theme.muted)
                        }
                    }
                }
            }
            Section("Planned titles") {
                Toggle(isOn: $filters.keepPlanned) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Keep planned titles")
                        Text("Only exclude what you started or finished").font(.caption).foregroundStyle(Theme.muted)
                    }
                }
                .onChange(of: filters.keepPlanned) { _, _ in filters.save() }
            }
        }
        .navigationTitle("Exclude from results")
    }
}

struct ForYouPage: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        @Bindable var app = app
        List {
            Section("For you") {
                Stepper(value: $app.forYouSeeds, in: 1...20) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Seed titles: \(app.forYouSeeds)")
                        Text("Fewer makes the feed more specific").font(.caption).foregroundStyle(Theme.muted)
                    }
                }
                Toggle(isOn: $app.forYouRatings) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Weight by my ratings")
                        Text("Higher-rated titles seed more often").font(.caption).foregroundStyle(Theme.muted)
                    }
                }
            }
        }
        .navigationTitle("For you")
    }
}

struct DisplayPage: View {
    @Environment(Filters.self) private var filters

    var body: some View {
        @Bindable var filters = filters
        List {
            Section {
                Picker("Title language", selection: $filters.romajiTitles) {
                    Text("English").tag(false)
                    Text("Romaji / original").tag(true)
                }
                .pickerStyle(.segmented)
                .onChange(of: filters.romajiTitles) { _, _ in filters.save() }
            } header: {
                Text("Title language")
            } footer: {
                Text("English is used when a title has one.")
            }
        }
        .navigationTitle("Display")
    }
}

// MARK: - Media

/// Which tabs to show. Nothing here names a medium: the datasets and the
/// media each fills come from GET /datasets, so a medium added on the
/// server appears without an app update.
struct MediaPage: View {
    @Environment(AppModel.self) private var app
    @Environment(Filters.self) private var filters
    @Environment(SettingsModel.self) private var model
    @State private var message: String?
    @State private var pendingDownload: (dataset: Dataset, next: [String])?

    var body: some View {
        List {
            Section {
                Text("Choose which tabs to show. Turning on a medium that isn't downloaded starts the download; turning one off only hides its tab.")
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
                    .listRowBackground(Color.clear)
                if let message {
                    Text(message).font(.footnote).foregroundStyle(Theme.bad).listRowBackground(Color.clear)
                }
            }
            ForEach(model.datasets) { dataset in
                Section {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(dataset.label).font(.headline)
                        Text(installText(dataset))
                            .font(.caption)
                            .foregroundStyle(dataset.install?.state == "failed" ? Theme.bad
                                             : dataset.busy ? Theme.accentText : Theme.muted)
                        if let install = dataset.install, install.state == "running",
                           ["downloading", "fetching"].contains(install.phase ?? ""),
                           let total = install.total, total > 0 {
                            ProgressView(value: Double(install.bytes ?? 0), total: Double(total))
                        }
                    }
                    .padding(.vertical, 4)
                    ForEach(dataset.media, id: \.self) { medium in
                        Toggle(Filters.label(for: medium), isOn: Binding(
                            get: { filters.visibleMedia.contains(medium) },
                            set: { on in toggle(medium, on: on, dataset: dataset) }
                        ))
                    }
                    if dataset.installable && !dataset.busy && (dataset.install?.state == "failed" || dataset.installed) {
                        Button(dataset.install?.state == "failed" ? "Retry"
                               : (dataset.updateLabel?.isEmpty == false ? dataset.updateLabel! : "Update from the newest release")) {
                            Task { await install(dataset) }
                        }
                    }
                }
            }
        }
        .navigationTitle("Media")
        // Re-polls while an install is under way and this page is open.
        .task {
            while !Task.isCancelled {
                do {
                    try await model.reloadDatasets(app)
                    message = nil
                } catch {
                    message = error.localizedDescription
                }
                guard model.datasets.contains(where: \.busy) else { break }
                try? await Task.sleep(nanoseconds: 4_000_000_000)
            }
        }
        .alert(pendingDownload.map { "Download \($0.dataset.label)?" } ?? "",
               isPresented: Binding(get: { pendingDownload != nil }, set: { if !$0 { pendingDownload = nil } })) {
            Button("Cancel", role: .cancel) { pendingDownload = nil }
            Button("Download") {
                if let pending = pendingDownload { Task { await save(pending.next) } }
                pendingDownload = nil
            }
        } message: {
            Text(pendingDownload?.dataset.downloadHint ?? "")
        }
    }

    private func toggle(_ medium: String, on: Bool, dataset: Dataset) {
        let current = filters.visibleMedia
        let next = on
            ? (current.contains(medium) ? current : current + [medium])
            : current.filter { $0 != medium }
        guard !next.isEmpty else {
            message = "At least one medium has to stay on."
            return
        }
        // Asked before, never after: switching this on is what starts it.
        if on && dataset.installable && !dataset.installed && !dataset.busy {
            pendingDownload = (dataset, next)
            return
        }
        Task { await save(next) }
    }

    private func save(_ next: [String]) async {
        do {
            var body = AppSettingsIn()
            body.enabledMedia = next
            try await model.apply(body, app: app)
            try await model.reloadDatasets(app)
            message = nil
        } catch {
            message = error.localizedDescription
        }
    }

    private func install(_ dataset: Dataset) async {
        guard let client = app.client else { return }
        do {
            _ = try await client.installDataset(dataset.key)
            try await model.reloadDatasets(app)
        } catch {
            message = error.localizedDescription
        }
    }

    private func gb(_ bytes: Int64) -> String { String(format: "%.2f GB", Double(bytes) / 1e9) }

    /// The wording the web and Android settings pages use.
    private func installText(_ dataset: Dataset) -> String {
        if let install = dataset.install {
            let total = install.total ?? 0
            let done = install.bytes ?? 0
            switch install.state {
            case "running":
                switch install.phase {
                case "downloading":
                    return total > 0 ? "Downloading \(gb(done)) of \(gb(total))" : "Starting"
                case "fetching":
                    return total > 0 ? "Fetching \(done.formatted()) of \(total.formatted()) titles"
                        : "Fetching: \(done.formatted()) titles read"
                case "merging": return "Merging into the catalogue"
                case "indexing": return "Building the search index"
                default: return "Starting"
                }
            case "queued": return "Queued. The server starts it within a minute."
            case "waiting", "failed": return install.message ?? ""
            default: break
            }
        }
        if dataset.installed && !dataset.installable { return "\(dataset.items.formatted()) titles" }
        if dataset.installed {
            return "\(dataset.items.formatted()) installed, \(dataset.embedded.formatted()) with embeddings"
        }
        if dataset.releaseAvailable == false { return "Not published yet. It downloads as soon as it is." }
        return "Not downloaded. \(dataset.downloadHint ?? "")"
    }
}

// MARK: - Server

/// How to reach the server. Local to this device, unlike everything else.
struct ServerPage: View {
    @Environment(AppModel.self) private var app
    @Environment(SettingsModel.self) private var model
    @State private var url = ""
    @State private var token = ""
    @State private var status: String?
    @State private var tmdbKey = ""
    @State private var tmdbStatus: String?

    var body: some View {
        List {
            Section {
                TextField("http://192.168.1.10:8009", text: $url)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField("Access token (optional)", text: $token)
                Button("Save") { Task { await save() } }
                if let status {
                    Text(status).font(.footnote).foregroundStyle(Theme.muted)
                }
            } header: {
                Text("Server")
            } footer: {
                Text("The token is only needed if the server was started with MANGABRAIN_AUTH_TOKEN set. Leave it blank otherwise.")
            }
            Section {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Access token")
                        Text(app.token.isEmpty ? "Not saved" : "Saved on this device")
                            .font(.caption).foregroundStyle(Theme.muted)
                    }
                    Spacer()
                    if !app.token.isEmpty {
                        Button("Forget", role: .destructive) {
                            app.setToken("")
                            token = ""
                            status = "Access token forgotten."
                        }
                    }
                }
            }
            Section {
                SecureField(model.settings?.tmdbApiKeySet == true ? "Saved"
                            : "v3 key or read access token from themoviedb.org", text: $tmdbKey)
                Button("Save key") { Task { await saveTmdb() } }
                if let tmdbStatus {
                    Text(tmdbStatus).font(.footnote).foregroundStyle(Theme.muted)
                }
            } header: {
                Text("Films and extra TV (TMDB)")
            } footer: {
                Text("Saved on the server, which fetches films with it, and the TV shows TVmaze does not have. This product uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.")
            }
        }
        .navigationTitle("Server")
        .onAppear {
            url = app.serverURL
            token = app.token
        }
    }

    /// Saving is not the same as working, so this proves the server answers.
    private func save() async {
        let value = url.trimmed
        guard !value.isEmpty else {
            status = "Enter the server URL, for example http://192.168.1.10:8009"
            return
        }
        guard value.lowercased().hasPrefix("http://") || value.lowercased().hasPrefix("https://") else {
            status = "The URL has to start with http:// or https://"
            return
        }
        app.configure(server: value, token: token)
        status = "Checking…"
        guard let client = app.client else { return }
        do {
            let results = try await client.search([URLQueryItem(name: "limit", value: "1")])
            status = "Connected. The catalog answered with \(results.count) result(s)."
        } catch let error as APIError where error.status == 404 {
            status = "Reached a server, but it has no /search endpoint. Is that the MangaBrain URL?"
        } catch {
            status = error.localizedDescription
        }
    }

    private func saveTmdb() async {
        let value = tmdbKey.trimmed
        guard !value.isEmpty else {
            tmdbStatus = "Paste your API key first."
            return
        }
        do {
            var body = AppSettingsIn()
            body.tmdbApiKey = value
            try await model.apply(body, app: app)
            tmdbKey = ""
            tmdbStatus = "Saved."
        } catch {
            tmdbStatus = error.localizedDescription
        }
    }
}
