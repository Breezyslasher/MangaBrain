import SwiftUI

/// Weights, filters, genres, tags and the three toggles.
///
/// Nothing here fires a request on change: on a phone a live re-rank would
/// be a request per slider step, so edits are collected and Apply runs once.
struct FiltersView: View {
    @Environment(AppModel.self) private var app
    @Environment(Filters.self) private var filters
    @Environment(DiscoverModel.self) private var discover

    @State private var genres: [String] = []
    @State private var tagVocabulary: [String] = []
    @State private var tagStatus: String?
    @State private var facets: FacetsResponse?
    @State private var dubLanguages: [DubLanguage] = []
    @State private var tagInput = ""

    /// The lists depend on the medium and on the adult toggle.
    private var vocabularyKey: String { "\(filters.medium)|\(filters.adult)|\(app.generation)" }

    var body: some View {
        @Bindable var filters = filters
        NavigationStack {
            Form {
                weightsSection
                rangeSection
                pickersSection
                genresSection
                tagsSection
                Section {
                    Toggle("Hide sequels and spin-offs", isOn: $filters.excludeFranchise)
                    Toggle("Cross-media suggestions", isOn: $filters.crossMedia)
                    Toggle("Include adult content", isOn: $filters.adult)
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.bg)
            .scrollDismissesKeyboard(.immediately)
            .navigationTitle("Filters · \(Filters.label(for: filters.medium))")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Reset") { filters.reset() }
                        .foregroundStyle(Theme.bad)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Apply") { apply() }
                        .fontWeight(.semibold)
                }
            }
            .safeAreaInset(edge: .bottom) {
                Button {
                    apply()
                } label: {
                    Text("Apply filters").frame(maxWidth: .infinity, minHeight: 32)
                }
                .buttonStyle(.borderedProminent)
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .background(Theme.bg.opacity(0.95))
            }
            .task(id: vocabularyKey) { await loadVocabularies() }
        }
    }

    private func apply() {
        filters.save()
        app.tab = .discover
        discover.rerun()
    }

    // MARK: - Sections

    private var weightsSection: some View {
        @Bindable var filters = filters
        return Section("Weights") {
            weight("Synopsis", $filters.wSemantic, Theme.wSynopsis)
            weight("Tags", $filters.wTags, Theme.wTags)
            weight("Genres", $filters.wGenres, Theme.wGenres)
            weight("My taste", $filters.wTaste, Theme.wTaste)
        }
    }

    private func weight(_ label: String, _ value: Binding<Double>, _ tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(label)
                Spacer()
                Text(String(Int(value.wrappedValue.rounded())))
                    .font(.body.monospacedDigit())
                    .foregroundStyle(Theme.muted)
            }
            Slider(value: value, in: 0...100, step: 1).tint(tint)
        }
    }

    private var rangeSection: some View {
        @Bindable var filters = filters
        return Section("Range") {
            HStack {
                numberField("Year from", $filters.yearMin)
                numberField("Year to", $filters.yearMax)
            }
            if filters.supports("min_score") {
                numberField("Min score", $filters.minScore)
            }
            if filters.supports("length") {
                HStack {
                    numberField(filters.medium == "anime" ? "Min episodes" : "Min chapters", $filters.minLen)
                    numberField(filters.medium == "anime" ? "Max episodes" : "Max chapters", $filters.maxLen)
                }
            }
        }
    }

    private func numberField(_ prompt: String, _ text: Binding<String>) -> some View {
        TextField(prompt, text: text)
            .keyboardType(.numberPad)
    }

    private var pickersSection: some View {
        @Bindable var filters = filters
        let known = facets ?? FacetsResponse()
        let formats = known.formats ?? []
        let countries = (known.countries ?? []).sorted { countryName($0) < countryName($1) }
        let statuses = (known.statuses ?? []).sorted { filters.statusRank($0) < filters.statusRank($1) }
        return Section {
            // One format (every film is a film) is no choice, so it is hidden.
            if formats.count > 1 {
                Picker("Format", selection: $filters.format) {
                    Text("Any").tag("")
                    ForEach(formats, id: \.self) { Text($0.replacingOccurrences(of: "_", with: " ")).tag($0) }
                }
            }
            if filters.supports("country") && countries.count > 1 {
                Picker("Origin", selection: $filters.country) {
                    Text("Any").tag("")
                    ForEach(countries, id: \.self) { Text(countryName($0)).tag($0) }
                }
            }
            if filters.supports("status") && statuses.count > 1 {
                Picker("Status", selection: $filters.status) {
                    Text("Any").tag("")
                    ForEach(statuses, id: \.self) { Text(filters.statusLabel($0)).tag($0) }
                }
            }
            if filters.supports("max_popularity") {
                Picker("Max popularity", selection: $filters.maxPopularity) {
                    Text("Any").tag("")
                    Text("Under 200k").tag("200000")
                    Text("Under 50k").tag("50000")
                    Text("Under 10k").tag("10000")
                    Text("Under 2k").tag("2000")
                }
            }
            if filters.supports("dub_lang") && !dubLanguages.isEmpty {
                Picker("Dub language", selection: $filters.dubLang) {
                    Text("Any").tag("")
                    ForEach(dubLanguages, id: \.self) { lang in
                        Text("\(lang.code.uppercased()) (\(lang.titles))").tag(lang.code)
                    }
                }
            }
        }
    }

    private var genresSection: some View {
        Section {
            if genres.isEmpty {
                Text("No genres for this medium.").foregroundStyle(Theme.muted)
            } else {
                FlowLayout(spacing: 6) {
                    ForEach(genres, id: \.self) { genre in
                        Button { cycle(genre) } label: { stateChip(genre, filters.genreState[genre]) }
                            .buttonStyle(.plain)
                    }
                }
                .padding(.vertical, 4)
            }
        } header: {
            Text("Genres")
        } footer: {
            Text("Tap to require, again to exclude, again to clear.")
        }
    }

    private var tagsSection: some View {
        let typed = tagInput.trimmed.lowercased()
        let suggestions = typed.isEmpty ? [] : Array(
            tagVocabulary
                .filter { $0.lowercased().contains(typed) && filters.tagState[$0] == nil }
                .sorted { a, b in
                    let pa = a.lowercased().hasPrefix(typed), pb = b.lowercased().hasPrefix(typed)
                    return pa != pb ? pa : a < b
                }
                .prefix(8))
        return Section {
            TextField("e.g. Time Travel", text: $tagInput)
                .textInputAutocapitalization(.words)
                .autocorrectionDisabled()
            ForEach(suggestions, id: \.self) { tag in
                Button(tag) { tagInput = tag }
                    .foregroundStyle(Theme.accentText)
            }
            HStack {
                Button("Require") { addTag("inc") }
                    .buttonStyle(.bordered)
                    .tint(Theme.ok)
                Button("Exclude") { addTag("exc") }
                    .buttonStyle(.bordered)
                    .tint(Theme.bad)
            }
            if !filters.tagState.isEmpty {
                FlowLayout(spacing: 6) {
                    ForEach(filters.tagState.keys.sorted(), id: \.self) { tag in
                        Button {
                            filters.tagState[tag] = nil
                        } label: {
                            stateChip(tag, filters.tagState[tag], closable: true)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.vertical, 4)
            }
        } header: {
            Text("Tags")
        } footer: {
            if let tagStatus { Text(tagStatus) }
        }
    }

    private func stateChip(_ label: String, _ state: String?, closable: Bool = false) -> some View {
        switch state {
        case "inc": return Chip(text: label, foreground: Theme.ok, background: Theme.okBg, closable: closable)
        case "exc": return Chip(text: label, foreground: Theme.bad, background: Theme.badBg, closable: closable)
        default: return Chip(text: label, foreground: Theme.muted, closable: closable)
        }
    }

    private func cycle(_ genre: String) {
        switch filters.genreState[genre] {
        case nil: filters.genreState[genre] = "inc"
        case "inc": filters.genreState[genre] = "exc"
        default: filters.genreState[genre] = nil
        }
    }

    private func addTag(_ state: String) {
        let tag = tagInput.trimmed
        guard !tag.isEmpty else { return }
        filters.tagState[tag] = state
        tagInput = ""
    }

    private func countryName(_ code: String) -> String {
        Locale(identifier: "en_US").localizedString(forRegionCode: code) ?? code
    }

    // MARK: - Loading

    /// Genres, tags and the picker choices all belong to a medium: TV names
    /// its genres "Legal" and "Medical", books "Biography".
    private func loadVocabularies() async {
        let medium = filters.medium
        let adult = filters.adult
        facets = nil
        genres = (medium == "anime" || medium == "manga") ? Filters.anilistGenres : []
        guard let client = app.client else {
            tagStatus = "Tag suggestions need the server URL."
            return
        }
        async let genreList = try? client.genres(medium: medium, adult: adult)
        async let facetList = try? client.facets(medium: medium, adult: adult)
        async let dubList = try? client.dubLanguages(minTitles: 10)
        let tagResult: Result<[String], Error>
        do {
            tagResult = .success(try await client.tags(medium: medium, adult: adult))
        } catch {
            tagResult = .failure(error)
        }
        let (g, f, d) = await (genreList, facetList, dubList)
        guard medium == filters.medium, adult == filters.adult else { return }

        if let g {
            genres = g
            // A genre this medium does not have would filter everything out.
            for key in filters.genreState.keys where !g.contains(key) { filters.genreState[key] = nil }
        }
        if let f {
            facets = f
            // Likewise a choice the medium does not offer.
            if !filters.format.isEmpty && !(f.formats ?? []).contains(filters.format) { filters.format = "" }
            if !filters.country.isEmpty && !(f.countries ?? []).contains(filters.country) { filters.country = "" }
            if !filters.status.isEmpty && !(f.statuses ?? []).contains(filters.status) { filters.status = "" }
        }
        if let d { dubLanguages = d }
        switch tagResult {
        case .success(let tags):
            tagVocabulary = tags
            tagStatus = tags.isEmpty
                ? "This medium has no tags to suggest; its genres and descriptions carry it." : nil
        case .failure(let error):
            tagStatus = "Tag suggestions unavailable: \(error.localizedDescription). Typing an exact tag still works."
        }
    }
}
