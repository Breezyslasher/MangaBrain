import Foundation
import Observation
import SwiftUI

enum AppTab: Hashable {
    case discover, filters, mix, settings
}

/// The list sources, by the name the server's routes and exclude_list use.
enum Account: String, CaseIterable, Identifiable, Hashable {
    case anilist, mal, kitsu, yamtrack, hardcover

    var id: String { rawValue }

    var label: String {
        switch self {
        case .anilist: return "AniList"
        case .mal: return "MyAnimeList"
        case .kitsu: return "Kitsu"
        case .yamtrack: return "Yamtrack"
        case .hardcover: return "Hardcover"
        }
    }

    var short: String {
        switch self {
        case .anilist: return "AL"
        case .mal: return "MAL"
        case .kitsu: return "K"
        case .yamtrack: return "Y"
        case .hardcover: return "H"
        }
    }

    var color: Color {
        switch self {
        case .anilist: return Color(hex: 0x02A9FF)
        case .mal: return Color(hex: 0x2E51A2)
        case .kitsu: return Color(hex: 0xFD755C)
        case .yamtrack: return Color(hex: 0x2B2B2E)
        case .hardcover: return Color(hex: 0x6366F1)
        }
    }
}

enum SettingsPage: Hashable {
    case profile, accounts, account(Account), exclude, forYou, media, display, server
}

/// App-wide state: how to reach the server, the filters, the mix, and
/// which tab is open.
@MainActor
@Observable
final class AppModel {
    static let defaultProfile = "default"
    static let mixMax = 5

    var tab: AppTab = .discover
    var settingsPath: [SettingsPage] = []

    private(set) var serverURL: String
    private(set) var token: String
    private(set) var profile: String

    let filters: Filters
    let discover: DiscoverModel

    /// Seed titles for a multi-seed recommendation, held for the session.
    /// Capped at five: past a handful the averaged embedding stops
    /// resembling any of them.
    var mix: [Media] = []

    /// For-you sampling, edited in Settings > For you.
    var forYouSeeds: Int {
        didSet { UserDefaults.standard.set(forYouSeeds, forKey: "foryou.seeds") }
    }
    var forYouRatings: Bool {
        didSet { UserDefaults.standard.set(forYouRatings, forKey: "foryou.ratings") }
    }

    /// Bumped when the server or profile changes, so screens reload.
    private(set) var generation = 0

    init() {
        let d = UserDefaults.standard
        filters = Filters()
        discover = DiscoverModel()
        serverURL = d.string(forKey: "server.url") ?? ""
        profile = d.string(forKey: "server.profile") ?? Self.defaultProfile
        token = Keychain.load() ?? ""
        forYouSeeds = d.object(forKey: "foryou.seeds") as? Int ?? 8
        forYouRatings = d.object(forKey: "foryou.ratings") as? Bool ?? true
        discover.app = self
        if serverURL.isEmpty {
            tab = .settings
            settingsPath = [.server]
        }
    }

    var isConfigured: Bool { !serverURL.isEmpty }

    var client: APIClient? {
        guard isConfigured else { return nil }
        return APIClient(baseURL: serverURL, token: token,
                         profile: profile == Self.defaultProfile ? nil : profile)
    }

    func configure(server: String, token newToken: String) {
        serverURL = server.trimmed.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        UserDefaults.standard.set(serverURL, forKey: "server.url")
        setToken(newToken)
    }

    func setToken(_ value: String) {
        token = value.trimmed
        if token.isEmpty { Keychain.delete() } else { Keychain.save(token) }
        generation += 1
    }

    /// Uses another profile from now on; everything is fetched again as it.
    func useProfile(_ name: String) {
        profile = name.trimmed.lowercased()
        UserDefaults.standard.set(profile, forKey: "server.profile")
        discover.clearAll()
        generation += 1
    }

    /// Returns false when the title is already in the mix or it is full.
    @discardableResult
    func addToMix(_ media: Media) -> Bool {
        guard !mix.contains(where: { $0.id == media.id }), mix.count < Self.mixMax else { return false }
        mix.append(media)
        return true
    }

    /// The tabs follow the server's media selection.
    func refreshSettings() async {
        guard let client else { return }
        if let settings = try? await client.settings() {
            filters.applySettings(settings)
            discover.mediaChanged()
        }
    }
}
