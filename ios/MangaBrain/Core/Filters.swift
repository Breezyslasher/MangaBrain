import Foundation
import Observation

/// Every query knob, and the parameters built from them.
///
/// A port of the Android app's FilterState, which mirrors the web client's
/// filterParams() and weightParams(): the three clients must ask the server
/// the same questions. Weights are percentages here and fractions on the
/// wire. Blank values are dropped, never sent empty: FastAPI reads "" for an
/// int parameter as a 422, not as absent.
@MainActor
@Observable
final class Filters {
    static let mediumLabels: [(key: String, label: String)] = [
        ("anime", "Anime"),
        ("manga", "Manga"),
        ("light_novel", "Light Novels"),
        ("one_shot", "One-Shots"),
        ("book", "Books"),
        ("movie", "Movies"),
        ("tv", "TV Shows"),
    ]

    static func label(for medium: String) -> String {
        mediumLabels.first { $0.key == medium }?.label ?? medium
    }

    /// AniList's genres: what anime and manga show until the server's list
    /// for the medium arrives. Other media get theirs from the server only.
    static let anilistGenres = [
        "Action", "Adventure", "Comedy", "Drama", "Ecchi", "Fantasy", "Horror",
        "Mahou Shoujo", "Mecha", "Music", "Mystery", "Psychological", "Romance",
        "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller",
    ]

    static let defaultWeights = (semantic: 50.0, tags: 30.0, genres: 20.0, taste: 0.0)

    var medium = "anime"
    var romajiTitles = false
    /// The server's media selection (GET /settings). Empty until it answers.
    var enabledMedia: [String] = []

    var wSemantic = defaultWeights.semantic
    var wTags = defaultWeights.tags
    var wGenres = defaultWeights.genres
    var wTaste = defaultWeights.taste

    var yearMin = ""
    var yearMax = ""
    var minScore = ""
    var minLen = ""
    var maxLen = ""
    var format = ""
    var country = ""
    var status = ""
    var maxPopularity = ""
    var dubLang = ""

    var adult = false
    var crossMedia = false
    var excludeFranchise = true
    var keepPlanned = false

    /// name -> "inc" | "exc", the web client's tri-state chips.
    var genreState: [String: String] = [:]
    var tagState: [String: String] = [:]

    var excluded: Set<Account> = []

    // Filled from GET /settings so the exclusion parameters can carry them.
    var anilistUser = ""
    var malUser = ""

    init() { load() }

    /// The tabs to show: the selection, or every tab before it is known.
    var visibleMedia: [String] {
        let all = Self.mediumLabels.map(\.key)
        let shown = all.filter { enabledMedia.isEmpty || enabledMedia.contains($0) }
        return shown.isEmpty ? all : shown
    }

    /// Controls a medium has no data behind. The value is suppressed, not
    /// just the control hidden: a country typed on Anime would otherwise
    /// still go out on Books and match no book at all.
    private static let unsupported: [String: Set<String>] = [
        "book": ["min_score", "country", "status", "max_popularity", "length"],
        "movie": ["min_score", "max_popularity", "length"],
        "tv": ["min_score", "max_popularity", "length"],
    ]

    func supports(_ control: String) -> Bool {
        if control == "dub_lang" { return medium == "anime" }
        return !(Self.unsupported[medium] ?? []).contains(control)
    }

    /// How many filters differ from their defaults, for the tab badge.
    var activeCount: Int {
        let counted: [(String, String)] = [
            (yearMin, "year"), (yearMax, "year"), (format, "format"),
            (minScore, "min_score"), (country, "country"), (status, "status"),
            (maxPopularity, "max_popularity"), (minLen, "length"), (maxLen, "length"),
            (dubLang, "dub_lang"),
        ]
        var count = counted.filter { !$0.0.isBlank && supports($0.1) }.count
        if adult { count += 1 }
        if crossMedia { count += 1 }
        if !excludeFranchise { count += 1 }
        return count + genreState.count + tagState.count
    }

    // MARK: - Parameters

    func filterItems() -> [URLQueryItem] {
        var items: [URLQueryItem] = []
        func put(_ key: String, _ value: String) {
            let v = value.trimmingCharacters(in: .whitespaces)
            if !v.isEmpty { items.append(URLQueryItem(name: key, value: v)) }
        }
        if adult { put("adult", "true") }
        put("year_min", yearMin)
        put("year_max", yearMax)
        if supports("min_score") { put("min_score", minScore) }
        if supports("country") { put("country", country) }
        if supports("status") { put("status", status) }
        if supports("max_popularity") { put("max_popularity", maxPopularity) }
        if supports("length") {
            put(medium == "anime" ? "max_episodes" : "max_chapters", maxLen)
            put(medium == "anime" ? "min_episodes" : "min_chapters", minLen)
        }
        if supports("dub_lang") { put("dub_lang", dubLang) }
        if excluded.contains(.mal) { put("mal_user", malUser) }
        if excluded.contains(.anilist) { put("anilist_user", anilistUser) }
        if keepPlanned { put("keep_planned", "true") }
        return items
    }

    /// The repeated parameters. `excludeKey` is exclude_lists for /search
    /// and exclude_list everywhere else; /random takes no tag filters.
    func listItems(excludeKey: String, tags: Bool = true) -> [URLQueryItem] {
        var items: [URLQueryItem] = []
        if !format.isEmpty { items.append(URLQueryItem(name: "format", value: format)) }
        for (name, state) in genreState.sorted(by: { $0.key < $1.key }) {
            items.append(URLQueryItem(name: state == "inc" ? "genre_in" : "genre_ex", value: name))
        }
        if tags {
            for (name, state) in tagState.sorted(by: { $0.key < $1.key }) {
                items.append(URLQueryItem(name: state == "inc" ? "tag_in" : "tag_ex", value: name))
            }
        }
        for account in [Account.kitsu, .yamtrack, .hardcover] where excluded.contains(account) {
            items.append(URLQueryItem(name: excludeKey, value: account.rawValue))
        }
        return items
    }

    func weightItems() -> [URLQueryItem] {
        func fraction(_ value: Double) -> String { String(Double(Int(value.rounded())) / 100) }
        var items = [
            URLQueryItem(name: "w_semantic", value: fraction(wSemantic)),
            URLQueryItem(name: "w_tags", value: fraction(wTags)),
            URLQueryItem(name: "w_genres", value: fraction(wGenres)),
        ]
        if wTaste.rounded() > 0 { items.append(URLQueryItem(name: "w_taste", value: fraction(wTaste))) }
        return items
    }

    func recommendItems(limit: Int) -> [URLQueryItem] {
        weightItems() + filterItems() + listItems(excludeKey: "exclude_list") + [
            URLQueryItem(name: "cross_media", value: String(crossMedia)),
            URLQueryItem(name: "exclude_franchise", value: String(excludeFranchise)),
            URLQueryItem(name: "limit", value: String(limit)),
        ]
    }

    /// No weights: a description has no seed to weigh components against.
    func describeItems(limit: Int) -> [URLQueryItem] {
        filterItems() + listItems(excludeKey: "exclude_list") + [
            URLQueryItem(name: "medium", value: medium),
            URLQueryItem(name: "limit", value: String(limit)),
        ]
    }

    func searchItems(limit: Int) -> [URLQueryItem] {
        filterItems() + listItems(excludeKey: "exclude_lists") + [
            URLQueryItem(name: "medium", value: medium),
            URLQueryItem(name: "limit", value: String(limit)),
        ]
    }

    // MARK: - Status words

    private static let statusOrder = ["FINISHED", "RELEASING", "NOT_YET_RELEASED", "HIATUS", "CANCELLED"]

    func statusRank(_ status: String) -> Int {
        Self.statusOrder.firstIndex(of: status) ?? Self.statusOrder.count
    }

    /// Every source is stored as AniList's five values, but "Releasing"
    /// means airing for a show and publishing for a book.
    func statusLabel(_ status: String) -> String {
        let labels: [String]
        switch medium {
        case "anime": labels = ["Finished", "Airing", "Not yet aired", "On hiatus", "Cancelled"]
        case "movie": labels = ["Released", "Releasing", "Upcoming", "On hold", "Cancelled"]
        case "tv": labels = ["Ended", "Airing", "In development", "To be determined", "Cancelled"]
        default: labels = ["Finished", "Publishing", "Not yet published", "On hiatus", "Cancelled"]
        }
        if let i = Self.statusOrder.firstIndex(of: status) { return labels[i] }
        return status.replacingOccurrences(of: "_", with: " ").lowercased()
    }

    func applySettings(_ settings: AppSettingsOut) {
        anilistUser = settings.anilistUsername ?? ""
        malUser = settings.malUsername ?? ""
        enabledMedia = settings.enabledMedia ?? []
    }

    // MARK: - Persistence

    private let defaults = UserDefaults.standard
    private static let prefix = "filters."

    /// Only what the Android app keeps across restarts; the rest resets.
    func load() {
        let d = defaults, p = Self.prefix
        medium = d.string(forKey: p + "medium") ?? "anime"
        wSemantic = d.object(forKey: p + "w_semantic") as? Double ?? Self.defaultWeights.semantic
        wTags = d.object(forKey: p + "w_tags") as? Double ?? Self.defaultWeights.tags
        wGenres = d.object(forKey: p + "w_genres") as? Double ?? Self.defaultWeights.genres
        wTaste = d.object(forKey: p + "w_taste") as? Double ?? Self.defaultWeights.taste
        adult = d.bool(forKey: p + "adult")
        crossMedia = d.bool(forKey: p + "cross_media")
        excludeFranchise = d.object(forKey: p + "exclude_franchise") as? Bool ?? true
        keepPlanned = d.bool(forKey: p + "keep_planned")
        romajiTitles = d.bool(forKey: p + "romaji_titles")
        let names = d.stringArray(forKey: p + "excluded") ?? []
        excluded = Set(names.compactMap(Account.init(rawValue:)))
    }

    func save() {
        let d = defaults, p = Self.prefix
        d.set(medium, forKey: p + "medium")
        d.set(wSemantic, forKey: p + "w_semantic")
        d.set(wTags, forKey: p + "w_tags")
        d.set(wGenres, forKey: p + "w_genres")
        d.set(wTaste, forKey: p + "w_taste")
        d.set(adult, forKey: p + "adult")
        d.set(crossMedia, forKey: p + "cross_media")
        d.set(excludeFranchise, forKey: p + "exclude_franchise")
        d.set(keepPlanned, forKey: p + "keep_planned")
        d.set(romajiTitles, forKey: p + "romaji_titles")
        d.set(excluded.map(\.rawValue).sorted(), forKey: p + "excluded")
    }

    /// Restores the weights too, so 50 / 30 / 20 is one tap away.
    func reset() {
        wSemantic = Self.defaultWeights.semantic
        wTags = Self.defaultWeights.tags
        wGenres = Self.defaultWeights.genres
        wTaste = Self.defaultWeights.taste
        yearMin = ""; yearMax = ""; minScore = ""; minLen = ""; maxLen = ""
        format = ""; country = ""; status = ""; maxPopularity = ""; dubLang = ""
        adult = false; crossMedia = false; excludeFranchise = true
        genreState = [:]; tagState = [:]
    }
}

extension String {
    var isBlank: Bool { trimmingCharacters(in: .whitespaces).isEmpty }
    var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
}
