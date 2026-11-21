import Foundation

// Response models, mirroring api/models.py. Decoded with
// convertFromSnakeCase, so `cover_image_large` arrives as coverImageLarge.
// Everything the server may omit is optional: MediaOut has 26 optional
// fields out of 28, and a missing one must parse rather than throw.

struct MediaLink: Codable, Hashable {
    let label: String
    let url: String
}

struct Media: Codable, Hashable, Identifiable {
    let id: Int
    let medium: String
    var idMal: Int?
    var title: String?
    var titleEnglish: String?
    var titleNative: String?
    var description: String?
    var genres: [String]?
    var tags: [String]?
    var format: String?
    var episodes: Int?
    var chapters: Int?
    var volumes: Int?
    var countryOfOrigin: String?
    var startYear: Int?
    var status: String?
    var averageScore: Int?
    var coverImage: String?
    var coverImageLarge: String?
    var isAdult: Bool?
    var hasEnglishDub: Bool?
    var dubSource: String?
    var links: [MediaLink]?

    /// English when available, unless Settings > Display asks for Romaji.
    func displayTitle(romaji: Bool) -> String {
        let order = romaji ? [title, titleEnglish, titleNative] : [titleEnglish, title, titleNative]
        return order.compactMap { $0 }.first { !$0.isEmpty } ?? "#\(id)"
    }

    /// Year / format / score, and the dub marker when the server asserts one.
    var displayMeta: String {
        var parts: [String] = []
        if let startYear { parts.append(String(startYear)) }
        if let format { parts.append(format.replacingOccurrences(of: "_", with: " ")) }
        if let averageScore { parts.append(String(averageScore)) }
        if hasEnglishDub == true { parts.append(dubSource == "franchise" ? "dub (series)" : "dub") }
        return parts.joined(separator: " / ")
    }

    var coverURL: URL? {
        guard let raw = coverImageLarge ?? coverImage else { return nil }
        return URL(string: raw)
    }
}

struct SearchResponse: Decodable {
    var results: [Media]?
}

/// A description-search hit. similarity is a raw cosine, not a calibrated
/// match score, so the grid shows facet coverage instead.
struct DescribeItem: Decodable {
    let media: Media
    var similarity: Double?
    var facetsHit: Int?
    var facetCount: Int?
}

struct DescribeResponse: Decodable {
    var facets: [String]?
    var results: [DescribeItem]?
}

struct ScoreComponents: Decodable {
    var semantic: Double?
    var tags: Double?
    var genres: Double?
    var taste: Double?
}

struct RecommendationItem: Decodable {
    let media: Media
    let similarity: Double
    var components: ScoreComponents?
}

struct RelatedItem: Decodable, Hashable {
    let media: Media
    let relationType: String
}

struct RecommendResponse: Decodable {
    let seed: Media
    var results: [RecommendationItem]?
    var related: [RelatedItem]?
    var seeds: [Media]?
}

// MARK: - Accounts, lists and vocabulary

struct AppSettingsOut: Decodable {
    var anilistUsername: String?
    var malUsername: String?
    var kitsuUsername: String?
    var yamtrackUrl: String?
    var yamtrackTokenSet: Bool?
    var hardcoverTokenSet: Bool?
    var tmdbApiKeySet: Bool?
    var profile: String?
    var enabledMedia: [String]?
}

/// PUT /settings. A nil field is left out of the body, so saving one
/// account leaves the others alone; an empty string clears it.
struct AppSettingsIn: Encodable {
    var anilistUsername: String?
    var malUsername: String?
    var kitsuUsername: String?
    var yamtrackUrl: String?
    var yamtrackToken: String?
    var hardcoverToken: String?
    var tmdbApiKey: String?
    var enabledMedia: [String]?
}

struct DatasetInstall: Decodable, Equatable {
    let state: String
    var phase: String?
    var bytes: Int64?
    var total: Int64?
    var message: String?
}

struct Dataset: Decodable, Equatable, Identifiable {
    let key: String
    let label: String
    let description: String
    let media: [String]
    var downloadHint: String?
    let installable: Bool
    let installed: Bool
    let items: Int
    let embedded: Int
    var install: DatasetInstall?
    var releaseAvailable: Bool?
    var updateLabel: String?

    var id: String { key }
    var busy: Bool { ["queued", "running", "waiting"].contains(install?.state ?? "") }
}

struct DatasetsOut: Decodable {
    let datasets: [Dataset]
}

struct UserListStatus: Decodable {
    let listType: String
    let entryCount: Int
    let fetchedAt: String
}

struct UserListsStatus: Decodable {
    let username: String
    var lists: [UserListStatus]?
}

struct ExclusionStatus: Decodable {
    let name: String
    let entryCount: Int
    let updatedAt: String
}

struct DubLanguage: Decodable, Hashable {
    let code: String
    let titles: Int
}

struct DubLanguagesOut: Decodable {
    var languages: [DubLanguage]?
}

struct TagsResponse: Decodable {
    var tags: [String]?
}

struct GenresResponse: Decodable {
    var genres: [String]?
}

struct FacetsResponse: Decodable {
    var countries: [String]?
    var formats: [String]?
    var statuses: [String]?
}

struct ProfileIn: Encodable {
    let name: String
}

struct ProfilesOut: Decodable {
    var profiles: [String]?
    var current: String?
}
