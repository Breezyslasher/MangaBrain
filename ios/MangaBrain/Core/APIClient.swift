import Foundation

struct APIError: LocalizedError {
    let status: Int?
    let message: String

    var errorDescription: String? { message }
}

/// Talks to a MangaBrain server.
///
/// Query parameter names must match the server's Query(...) names exactly:
/// FastAPI silently ignores an unknown one, so a typo is a filter that
/// quietly does not filter. Repeated parameters (format, genre_in, ...) are
/// why queries are lists of URLQueryItem rather than dictionaries.
///
/// Note /search takes `exclude_lists` while /recommend, /describe, /foryou
/// and /random take `exclude_list`. That asymmetry is the server's.
struct APIClient: Sendable {
    let baseURL: String
    let token: String?
    let profile: String?

    private static let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: config)
    }()

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    private static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    // MARK: - Transport

    func get<T: Decodable>(_ path: String, _ query: [URLQueryItem] = []) async throws -> T {
        try await send("GET", path, query: query)
    }

    func send<T: Decodable>(_ method: String, _ path: String,
                            query: [URLQueryItem] = [], body: (any Encodable)? = nil) async throws -> T {
        let data = try await raw(method, path, query: query, body: body)
        do {
            return try Self.decoder.decode(T.self, from: data)
        } catch {
            throw APIError(status: nil, message: "The server sent a reply this app could not read.")
        }
    }

    private func raw(_ method: String, _ path: String,
                     query: [URLQueryItem], body: (any Encodable)?) async throws -> Data {
        let base = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        guard var components = URLComponents(string: "\(base)/\(path)") else {
            throw APIError(status: nil, message: "Invalid server address.")
        }
        if !query.isEmpty { components.queryItems = query }
        // URLComponents leaves "+" alone in a query, which FastAPI reads as
        // a space; encode it so a "C++" tag survives the trip.
        components.percentEncodedQuery = components.percentEncodedQuery?
            .replacingOccurrences(of: "+", with: "%2B")
        guard let url = components.url else {
            throw APIError(status: nil, message: "Invalid server address.")
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        // Auth is opt-in on the server; an empty Bearer header is never sent.
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        // Whose accounts and lists. The default profile sends nothing.
        if let profile, !profile.isEmpty {
            request.setValue(profile, forHTTPHeaderField: "X-Profile")
        }
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try Self.encoder.encode(body)
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await Self.session.data(for: request)
        } catch {
            if (error as? URLError)?.code == .cancelled { throw CancellationError() }
            throw APIError(status: nil, message: "Could not reach the server: \(error.localizedDescription)")
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError(status: nil, message: "Unexpected response from the server.")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError(status: http.statusCode, message: Self.message(status: http.statusCode, body: data))
        }
        return data
    }

    /// FastAPI explains most failures in {"detail": ...}: a string, or for a
    /// 422 a list of {"msg": ...}. That text is what makes a 409 actionable.
    private static func message(status: Int, body: Data) -> String {
        if status == 401 { return "This server requires an access token. Enter it in Settings > Server." }
        var detail: String?
        if let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
            if let text = json["detail"] as? String {
                detail = text
            } else if let list = json["detail"] as? [[String: Any]] {
                detail = list.compactMap { $0["msg"] as? String }.joined(separator: "; ")
            }
        }
        if let detail, !detail.isEmpty { return "Server returned HTTP \(status): \(detail)" }
        return "Server returned HTTP \(status)."
    }

    private func segment(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(CharacterSet(charactersIn: "/"))) ?? value
    }

    // MARK: - Catalog

    func search(_ query: [URLQueryItem]) async throws -> [Media] {
        let response: SearchResponse = try await get("search", query)
        return response.results ?? []
    }

    func describe(_ query: [URLQueryItem]) async throws -> DescribeResponse {
        try await get("describe", query)
    }

    func recommend(_ id: Int, _ query: [URLQueryItem]) async throws -> RecommendResponse {
        try await get("recommend/\(id)", query)
    }

    /// Multi-seed: the mix, with `ids` in the query.
    func recommendMix(_ query: [URLQueryItem]) async throws -> RecommendResponse {
        try await get("recommend", query)
    }

    func forYou(_ query: [URLQueryItem]) async throws -> RecommendResponse {
        try await get("foryou", query)
    }

    /// Surprise me. /random has no tag filters, so callers leave them out.
    func random(_ query: [URLQueryItem]) async throws -> Media {
        try await get("random", query)
    }

    func tags(medium: String, adult: Bool) async throws -> [String] {
        let r: TagsResponse = try await get("tags", [.init(name: "medium", value: medium), .init(name: "adult", value: String(adult))])
        return r.tags ?? []
    }

    func genres(medium: String, adult: Bool) async throws -> [String] {
        let r: GenresResponse = try await get("genres", [.init(name: "medium", value: medium), .init(name: "adult", value: String(adult))])
        return r.genres ?? []
    }

    func facets(medium: String, adult: Bool) async throws -> FacetsResponse {
        try await get("facets", [.init(name: "medium", value: medium), .init(name: "adult", value: String(adult))])
    }

    func dubLanguages(minTitles: Int) async throws -> [DubLanguage] {
        let r: DubLanguagesOut = try await get("dub/languages", [.init(name: "min_titles", value: String(minTitles))])
        return r.languages ?? []
    }

    // MARK: - Settings, profiles, media

    func settings() async throws -> AppSettingsOut {
        try await get("settings")
    }

    func saveSettings(_ body: AppSettingsIn) async throws -> AppSettingsOut {
        try await send("PUT", "settings", body: body)
    }

    func profiles() async throws -> ProfilesOut {
        try await get("profiles")
    }

    func makeProfile(_ name: String) async throws -> ProfilesOut {
        try await send("POST", "profiles", body: ProfileIn(name: name))
    }

    func removeProfile(_ name: String) async throws -> ProfilesOut {
        try await send("DELETE", "profiles/\(segment(name))")
    }

    func datasets() async throws -> [Dataset] {
        let r: DatasetsOut = try await get("datasets")
        return r.datasets
    }

    func installDataset(_ key: String) async throws -> Dataset {
        try await send("POST", "datasets/\(segment(key))/install")
    }

    // MARK: - Lists

    func listStatus(_ account: Account, username: String) async throws -> ListStatus {
        switch account {
        case .anilist, .mal:
            let lists: UserListsStatus = try await get("\(account.rawValue)/\(segment(username))")
            return ListStatus(lists)
        case .kitsu, .yamtrack, .hardcover:
            let one: ExclusionStatus = try await get(account.rawValue)
            return ListStatus(one)
        }
    }

    func refresh(_ account: Account, username: String) async throws -> ListStatus {
        switch account {
        case .anilist, .mal:
            let lists: UserListsStatus = try await send("POST", "\(account.rawValue)/\(segment(username))/refresh")
            return ListStatus(lists)
        case .kitsu, .yamtrack, .hardcover:
            let one: ExclusionStatus = try await send("POST", "\(account.rawValue)/refresh")
            return ListStatus(one)
        }
    }
}

/// What the server last reported for a synced list.
struct ListStatus: Equatable {
    let syncedAt: Date?
    let tiles: [Tile]

    struct Tile: Equatable, Hashable {
        let name: String
        let count: Int
    }

    /// One tile per list type: AniList and MAL report anime and manga.
    init(_ status: UserListsStatus) {
        let lists = status.lists ?? []
        syncedAt = lists.compactMap { parseISODate($0.fetchedAt) }.max()
        tiles = lists.map { Tile(name: $0.listType.capitalized, count: $0.entryCount) }
    }

    /// Kitsu, Yamtrack and Hardcover report one count.
    init(_ status: ExclusionStatus) {
        syncedAt = parseISODate(status.updatedAt)
        tiles = [Tile(name: "Entries", count: status.entryCount)]
    }
}

/// The server writes Python isoformat(): microseconds, and an offset only
/// sometimes. Tries the shapes it produces, treating a bare time as UTC.
func parseISODate(_ value: String?) -> Date? {
    guard var text = value, !text.isEmpty else { return nil }
    let hasZone = text.hasSuffix("Z") || text.range(of: #"[+-]\d\d:?\d\d$"#, options: .regularExpression) != nil
    if !hasZone { text += "Z" }
    let fractional = ISO8601DateFormatter()
    fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = fractional.date(from: text) { return date }
    let plain = ISO8601DateFormatter()
    plain.formatOptions = [.withInternetDateTime]
    if let date = plain.date(from: text) { return date }
    // More than three fractional digits: cut them to three and retry.
    if let dot = text.firstIndex(of: ".") {
        let tailStart = text[dot...].firstIndex { !$0.isNumber && $0 != "." } ?? text.endIndex
        let digits = text[text.index(after: dot)..<tailStart].prefix(3)
        let trimmed = String(text[..<dot]) + "." + String(digits) + String(text[tailStart...])
        return fractional.date(from: trimmed)
    }
    return nil
}

func timeAgo(_ date: Date) -> String {
    let seconds = max(0, Date().timeIntervalSince(date))
    if seconds < 60 { return "just now" }
    let minutes = Int(seconds / 60)
    if minutes < 60 { return minutes == 1 ? "1 minute ago" : "\(minutes) minutes ago" }
    let hours = minutes / 60
    if hours < 24 { return hours == 1 ? "1 hour ago" : "\(hours) hours ago" }
    let days = hours / 24
    return days == 1 ? "1 day ago" : "\(days) days ago"
}
