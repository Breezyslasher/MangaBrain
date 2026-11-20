import Foundation
import Observation

/// What the Settings pages show: the server's account settings, each
/// list's last sync, and the datasets behind the media tabs.
@MainActor
@Observable
final class SettingsModel {
    private(set) var settings: AppSettingsOut?
    private(set) var statuses: [Account: ListStatus] = [:]
    private(set) var datasets: [Dataset] = []
    private(set) var loadError: String?
    var syncing: Set<Account> = []

    func username(_ account: Account) -> String {
        switch account {
        case .anilist: return settings?.anilistUsername ?? ""
        case .mal: return settings?.malUsername ?? ""
        case .kitsu: return settings?.kitsuUsername ?? ""
        case .yamtrack: return settings?.yamtrackUrl ?? ""
        case .hardcover: return ""
        }
    }

    /// Hardcover is token-only: connected means a token is saved.
    func connected(_ account: Account) -> Bool {
        account == .hardcover ? settings?.hardcoverTokenSet == true : !username(account).isEmpty
    }

    func displayName(_ account: Account) -> String {
        switch account {
        case .yamtrack:
            return username(account).replacingOccurrences(of: "https://", with: "")
                .replacingOccurrences(of: "http://", with: "")
        case .hardcover: return "Token saved"
        default: return username(account)
        }
    }

    var connectedAccounts: [Account] { Account.allCases.filter(connected) }

    var latestSync: Date? { connectedAccounts.compactMap { statuses[$0]?.syncedAt }.max() }

    func load(_ app: AppModel) async {
        guard let client = app.client else { return }
        do {
            let fresh = try await client.settings()
            settings = fresh
            app.filters.applySettings(fresh)
            app.discover.mediaChanged()
            loadError = nil
        } catch {
            loadError = error.localizedDescription
            return
        }
        for account in connectedAccounts {
            // A list never synced answers 404: "not synced", not an error.
            if let status = try? await client.listStatus(account, username: username(account)) {
                statuses[account] = status
            }
        }
        if let fresh = try? await client.datasets() { datasets = fresh }
    }

    func reloadDatasets(_ app: AppModel) async throws {
        guard let client = app.client else { return }
        let fresh = try await client.settings()
        settings = fresh
        app.filters.applySettings(fresh)
        datasets = try await client.datasets()
    }

    /// Saves one account and nothing else; an empty string clears it.
    func save(_ account: Account, value: String?, yamtrackToken: String? = nil, app: AppModel) async throws {
        guard let client = app.client else { throw APIError(status: nil, message: "Set the server URL first.") }
        var body = AppSettingsIn()
        switch account {
        case .anilist: body.anilistUsername = value
        case .mal: body.malUsername = value
        case .kitsu: body.kitsuUsername = value
        case .yamtrack:
            body.yamtrackUrl = value
            body.yamtrackToken = yamtrackToken
        case .hardcover: body.hardcoverToken = value
        }
        try await apply(body, app: app)
        if value == "" { statuses[account] = nil }
    }

    func apply(_ body: AppSettingsIn, app: AppModel) async throws {
        guard let client = app.client else { throw APIError(status: nil, message: "Set the server URL first.") }
        let saved = try await client.saveSettings(body)
        settings = saved
        app.filters.applySettings(saved)
        app.discover.mediaChanged()
    }

    func sync(_ account: Account, app: AppModel) async throws {
        guard let client = app.client else { return }
        syncing.insert(account)
        defer { syncing.remove(account) }
        statuses[account] = try await client.refresh(account, username: username(account))
    }

    func reset() {
        settings = nil
        statuses = [:]
        datasets = []
    }
}
