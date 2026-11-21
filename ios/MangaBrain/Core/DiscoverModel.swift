import Foundation
import Observation

/// Search, browse, description search and recommendations, in one pane.
///
/// A port of the Android DiscoverFragment. A seed and its results are the
/// same view with the seed card added, so drilling in keeps the medium, the
/// filters and the place in the list.
@MainActor
@Observable
final class DiscoverModel {
    /// Titles per page, and per Show more.
    static let page = 60
    /// The most /recommend, /recommend?ids and /foryou answer in one list.
    static let maxRecommendations = 200
    /// The server's min_length on a description.
    static let minDescription = 3

    struct Row {
        let media: Media
        var similarity: Double? = nil
        /// Overrides the percentage: a description search has no calibrated
        /// score, only how many of the query's facets found the title.
        var badge: String? = nil
    }

    enum Mode: Equatable {
        case seed(Int)
        case mix
        case forYou
        case browse(String, filtered: Bool)
        case describe(String)
    }

    weak var app: AppModel?

    var query = ""
    var describeText = ""

    private(set) var rows: [Row] = []
    private(set) var seed: Media?
    private(set) var related: [RelatedItem] = []
    private(set) var status: String?
    private(set) var mode: Mode?
    var flash: String?
    var expanded = false

    private(set) var moreAvailable = false
    private(set) var moreLoading = false
    private(set) var moreFailed = false

    /// Bumped to send the grid back to the top.
    private(set) var scrollToken = 0

    /// Bumped by every new list, so a Show more page that answers after the
    /// reader has moved on is dropped instead of landing under the new list.
    @ObservationIgnored private var pageSeq = 0
    @ObservationIgnored private var moreAction: (() async throws -> Void)?
    @ObservationIgnored private var searchTask: Task<Void, Never>?
    @ObservationIgnored private var flashTask: Task<Void, Never>?
    /// The seeds on screen: a re-rank of the same seeds keeps the scroll.
    @ObservationIgnored private var shownSeedIds: String?

    private var filters: Filters { app!.filters }

    private func client() -> APIClient? {
        guard let client = app?.client else {
            status = "Set the server URL first, in Settings > Server."
            return nil
        }
        return client
    }

    // MARK: - Entry points

    /// The title box: debounced, and without filters, like the other clients.
    func queryChanged() {
        searchTask?.cancel()
        let q = query.trimmed
        guard !q.isEmpty else { return }
        searchTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard !Task.isCancelled else { return }
            await self?.runSearch(q, filtered: false)
        }
    }

    func browse() {
        searchTask?.cancel()
        Task { await runSearch(query.trimmed, filtered: true) }
    }

    /// No debounce: every facet of a description is its own vector search.
    func describe() {
        Task { await runDescribe(describeText) }
    }

    func runSearch(_ q: String, filtered: Bool) async {
        guard let client = client() else { return }
        mode = .browse(q, filtered: filtered)
        status = "Searching…"
        let seq = newList()
        await call(seq) {
            let page = try await self.searchPage(client, q, filtered: filtered, offset: 0)
            guard seq == self.pageSeq else { return }
            self.seed = nil
            self.related = []
            self.rows = page.map { Row(media: $0) }
            self.newPage()
            self.status = page.isEmpty ? "No matches." : nil
            self.offerMoreSearch(seq, client, q, filtered: filtered, offset: page.count, last: page.count)
        }
    }

    func runDescribe(_ text: String) async {
        let q = text.trimmed
        guard q.count >= Self.minDescription, let client = client() else { return }
        mode = .describe(q)
        status = "Reading the catalog…"
        let seq = newList()
        await call(seq) {
            let response = try await client.describe(
                [URLQueryItem(name: "q", value: q)] + self.filters.describeItems(limit: Self.page))
            guard seq == self.pageSeq else { return }
            let multi = (response.facets?.count ?? 0) > 1
            self.seed = nil
            self.related = []
            self.rows = (response.results ?? []).map { item in
                Row(media: item.media,
                    badge: multi ? "\(item.facetsHit ?? 1)/\(item.facetCount ?? 1)" : nil)
            }
            self.newPage()
            self.status = self.rows.isEmpty
                ? "Nothing matches that description. Try fewer words, or loosen the filters." : nil
        }
    }

    func loadRecommendations(_ id: Int) {
        guard let client = client() else { return }
        mode = .seed(id)
        status = "Loading…"
        let filters = self.filters
        renderPaged { limit in
            try await client.recommend(id, filters.recommendItems(limit: limit))
        }
    }

    func loadMix() {
        guard let client = client(), let ids = app?.mix.map(\.id), !ids.isEmpty else { return }
        mode = .mix
        status = "Loading…"
        let filters = self.filters
        renderPaged { limit in
            try await client.recommendMix(
                ids.map { URLQueryItem(name: "ids", value: String($0)) } + filters.recommendItems(limit: limit))
        }
    }

    /// For you draws its seeds at random per request, so asking again for a
    /// longer list would be a different list: fetch the longest once and
    /// reveal it a page at a time.
    func loadForYou() {
        guard let client = client(), let app else { return }
        mode = .forYou
        status = "Loading…"
        let items = filters.recommendItems(limit: Self.maxRecommendations) + [
            URLQueryItem(name: "medium", value: filters.medium),
            URLQueryItem(name: "seeds", value: String(app.forYouSeeds)),
            URLQueryItem(name: "use_ratings", value: String(app.forYouRatings)),
        ]
        let seq = newList()
        Task {
            await call(seq) {
                let data = try await client.forYou(items)
                guard seq == self.pageSeq else { return }
                let all = data.results ?? []
                self.render(data, shown: Array(all.prefix(Self.page)))
                self.offerReveal(seq, all)
            }
        }
    }

    /// /random takes no tag filters, so a pick honours every other filter.
    func surprise() {
        guard let client = client() else { return }
        status = "Loading…"
        let seq = pageSeq
        let items = filters.filterItems()
            + filters.listItems(excludeKey: "exclude_list", tags: false)
            + [URLQueryItem(name: "medium", value: filters.medium)]
        Task {
            await call(seq) {
                let media = try await client.random(items)
                self.loadRecommendations(media.id)
            }
        }
    }

    /// Re-runs whatever is on screen, after a filter or medium change.
    func rerun() {
        switch mode {
        case .seed(let id): loadRecommendations(id)
        case .mix: loadMix()
        case .forYou: loadForYou()
        case .browse(let q, let filtered): Task { await runSearch(q, filtered: filtered) }
        case .describe(let q): Task { await runDescribe(q) }
        case nil: break
        }
    }

    func selectMedium(_ key: String) {
        guard key != filters.medium else { return }
        filters.medium = key
        // The format vocabulary is per medium.
        filters.format = ""
        filters.save()
        rerun()
    }

    /// Settings > Media changed the selection. A medium switched off leaves
    /// at once, with its results.
    func mediaChanged() {
        let visible = filters.visibleMedia
        if !visible.contains(filters.medium), let first = visible.first {
            filters.medium = first
            filters.format = ""
            clearAll()
        }
    }

    /// Requires one of the seed's genres or tags and re-ranks, the way
    /// tapping a chip on the web seed card does.
    func require(genre: String) {
        filters.genreState[genre] = "inc"
        show("Requiring genre: \(genre)")
        rerun()
    }

    func require(tag: String) {
        filters.tagState[tag] = "inc"
        show("Requiring tag: \(tag)")
        rerun()
    }

    func addToMix(_ media: Media) {
        guard let app else { return }
        let romaji = filters.romajiTitles
        show(app.addToMix(media)
             ? "Added \(media.displayTitle(romaji: romaji)) to the mix."
             : "The mix holds at most \(AppModel.mixMax) titles.")
    }

    func show(_ message: String) {
        flash = message
        flashTask?.cancel()
        flashTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 2_500_000_000)
            guard !Task.isCancelled else { return }
            self?.flash = nil
        }
    }

    func clearAll() {
        newList()
        mode = nil
        rows = []
        seed = nil
        related = []
        status = nil
        shownSeedIds = nil
    }

    // MARK: - Show more

    func showMore() {
        guard let action = moreAction, !moreLoading else { return }
        let seq = pageSeq
        moreLoading = true
        moreFailed = false
        Task {
            do {
                try await action()
            } catch {
                if seq == pageSeq {
                    moreFailed = true
                    status = error.localizedDescription
                }
            }
            if seq == pageSeq { moreLoading = false }
        }
    }

    private func offer(_ action: @escaping () async throws -> Void) {
        moreAction = action
        moreAvailable = true
    }

    private func withdraw() {
        moreAction = nil
        moreAvailable = false
    }

    private func searchPage(_ client: APIClient, _ q: String, filtered: Bool, offset: Int) async throws -> [Media] {
        var items = filtered
            ? filters.searchItems(limit: Self.page)
            : [URLQueryItem(name: "medium", value: filters.medium),
               URLQueryItem(name: "limit", value: String(Self.page))]
        items.append(URLQueryItem(name: "offset", value: String(offset)))
        if !q.isEmpty { items.append(URLQueryItem(name: "q", value: q)) }
        return try await client.search(items)
    }

    /// The server pages a search or browse with offset.
    private func offerMoreSearch(_ seq: Int, _ client: APIClient, _ q: String,
                                 filtered: Bool, offset: Int, last: Int) {
        guard last >= Self.page else { return withdraw() }
        offer {
            let page = try await self.searchPage(client, q, filtered: filtered, offset: offset)
            guard seq == self.pageSeq else { return }
            self.rows += page.map { Row(media: $0) }
            self.offerMoreSearch(seq, client, q, filtered: filtered, offset: offset + page.count, last: page.count)
        }
    }

    /// Recommendations have no offset: Show more asks for the list again,
    /// longer, and adds what is past the titles already shown. The ranking
    /// is deterministic, so the first part is the same list.
    private func renderPaged(_ fetch: @escaping (Int) async throws -> RecommendResponse) {
        let seq = newList()
        Task {
            await call(seq) {
                let data = try await fetch(Self.page)
                guard seq == self.pageSeq else { return }
                self.render(data, shown: data.results ?? [])
                self.offerMoreRecommendations(seq, fetch, limit: Self.page, got: data.results?.count ?? 0)
            }
        }
    }

    private func offerMoreRecommendations(_ seq: Int, _ fetch: @escaping (Int) async throws -> RecommendResponse,
                                          limit: Int, got: Int) {
        guard got >= limit, limit < Self.maxRecommendations else { return withdraw() }
        offer {
            let next = min(limit + Self.page, Self.maxRecommendations)
            let data = try await fetch(next)
            guard seq == self.pageSeq else { return }
            let added = (data.results ?? []).dropFirst(self.rows.count)
            self.rows += added.map { Row(media: $0.media, similarity: $0.similarity) }
            self.offerMoreRecommendations(seq, fetch, limit: next, got: data.results?.count ?? 0)
        }
    }

    private func offerReveal(_ seq: Int, _ all: [RecommendationItem]) {
        guard rows.count < all.count else { return withdraw() }
        offer {
            guard seq == self.pageSeq else { return }
            let next = all.dropFirst(self.rows.count).prefix(Self.page)
            self.rows += next.map { Row(media: $0.media, similarity: $0.similarity) }
            self.offerReveal(seq, all)
        }
    }

    // MARK: - Render

    private func render(_ data: RecommendResponse, shown: [RecommendationItem]) {
        if seed?.id != data.seed.id { expanded = false }
        seed = data.seed
        related = data.related ?? []
        rows = shown.map { Row(media: $0.media, similarity: $0.similarity) }
        status = rows.isEmpty ? "No recommendations pass the active filters." : nil
        let seeds = (data.seeds?.isEmpty == false ? data.seeds! : [data.seed])
        let ids = seeds.map { String($0.id) }.joined(separator: ",")
        if ids != shownSeedIds {
            shownSeedIds = ids
            scrollToken += 1
        }
    }

    /// A new result set is a new page: back to the top.
    private func newPage() {
        shownSeedIds = nil
        scrollToken += 1
    }

    @discardableResult
    private func newList() -> Int {
        pageSeq += 1
        withdraw()
        moreLoading = false
        moreFailed = false
        return pageSeq
    }

    /// One error path for every call. A cancelled or superseded request
    /// says nothing; it must not wipe the list that replaced it.
    private func call(_ seq: Int, _ body: () async throws -> Void) async {
        do {
            try await body()
        } catch is CancellationError {
            return
        } catch {
            guard seq == pageSeq else { return }
            newList()
            rows = []
            seed = nil
            related = []
            shownSeedIds = nil
            status = error.localizedDescription
        }
    }
}
