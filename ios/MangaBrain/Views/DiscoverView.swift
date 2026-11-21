import SwiftUI

struct DiscoverView: View {
    @Environment(AppModel.self) private var app
    @Environment(Filters.self) private var filters
    @Environment(DiscoverModel.self) private var discover

    private let columns = [GridItem(.adaptive(minimum: 104, maximum: 180), spacing: 10, alignment: .top)]

    var body: some View {
        @Bindable var discover = discover
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        Color.clear.frame(height: 0).id("top")
                        controls
                        if let status = discover.status {
                            Text(status)
                                .font(.footnote)
                                .foregroundStyle(Theme.muted)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        if let seed = discover.seed {
                            SeedCard(media: seed)
                        }
                        if !discover.related.isEmpty {
                            RelatedRow(items: discover.related)
                        }
                        if !discover.rows.isEmpty {
                            Text("\(discover.rows.count) matches")
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(Theme.muted)
                        }
                        LazyVGrid(columns: columns, spacing: 16) {
                            ForEach(Array(discover.rows.enumerated()), id: \.offset) { _, row in
                                MediaCard(row: row)
                            }
                        }
                        if discover.moreAvailable {
                            Button {
                                discover.showMore()
                            } label: {
                                Group {
                                    if discover.moreLoading {
                                        ProgressView()
                                    } else {
                                        Text(discover.moreFailed ? "Try again" : "Show more")
                                    }
                                }
                                .frame(maxWidth: .infinity, minHeight: 28)
                            }
                            .buttonStyle(.bordered)
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.bottom, 24)
                    .frame(maxWidth: 1100)
                    .frame(maxWidth: .infinity)
                }
                .scrollDismissesKeyboard(.immediately)
                .onChange(of: discover.scrollToken) { _, _ in
                    proxy.scrollTo("top", anchor: .top)
                }
            }
            .background(Theme.bg)
            .navigationTitle("Discover")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { mediumMenu }
            }
            .overlay(alignment: .bottom) {
                if let flash = discover.flash {
                    Text(flash)
                        .font(.footnote)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        .background(Theme.card, in: Capsule())
                        .overlay(Capsule().stroke(Theme.border))
                        .padding(.bottom, 12)
                        .transition(.opacity)
                }
            }
            .animation(.easeInOut(duration: 0.2), value: discover.flash)
        }
    }

    private var mediumMenu: some View {
        Menu {
            Picker("Medium", selection: Binding(
                get: { filters.medium },
                set: { discover.selectMedium($0) }
            )) {
                ForEach(filters.visibleMedia, id: \.self) { key in
                    Text(Filters.label(for: key)).tag(key)
                }
            }
        } label: {
            HStack(spacing: 4) {
                Text(Filters.label(for: filters.medium))
                Image(systemName: "chevron.down").font(.caption.weight(.bold))
            }
            .font(.subheadline.weight(.semibold))
        }
    }

    private var controls: some View {
        @Bindable var discover = discover
        return VStack(spacing: 10) {
            HStack(spacing: 8) {
                field(systemImage: "magnifyingglass", prompt: "Search titles…", text: $discover.query)
                    .onChange(of: discover.query) { _, _ in discover.queryChanged() }
                    .onSubmit { discover.queryChanged() }
                    .submitLabel(.search)
                Button("Browse") { discover.browse() }
                    .buttonStyle(.bordered)
            }
            HStack(spacing: 8) {
                field(systemImage: "text.bubble", prompt: "Describe what you want…", text: $discover.describeText)
                    .onSubmit { discover.describe() }
                    .submitLabel(.go)
                Button("Find") { discover.describe() }
                    .buttonStyle(.bordered)
            }
            HStack(spacing: 8) {
                Button {
                    discover.loadForYou()
                } label: {
                    Label("For you", systemImage: "sparkles").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                Button {
                    discover.surprise()
                } label: {
                    Label("Surprise me", systemImage: "shuffle").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }
        }
        .padding(.top, 6)
    }

    private func field(systemImage: String, prompt: String, text: Binding<String>) -> some View {
        HStack(spacing: 8) {
            Image(systemName: systemImage).foregroundStyle(Theme.muted)
            TextField(prompt, text: text)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            if !text.wrappedValue.isEmpty {
                Button {
                    text.wrappedValue = ""
                } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.dim)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 12)
        .frame(minHeight: 42)
        .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.border))
    }
}

/// One result: cover, match badge, add-to-mix, title and meta.
struct MediaCard: View {
    @Environment(Filters.self) private var filters
    @Environment(DiscoverModel.self) private var discover
    let row: DiscoverModel.Row

    var body: some View {
        let media = row.media
        VStack(alignment: .leading, spacing: 4) {
            Color.clear
                .aspectRatio(2.0 / 3.0, contentMode: .fit)
                .overlay(Cover(url: media.coverURL))
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(alignment: .topTrailing) {
                    if let label = row.badge ?? row.similarity.map({ "\(Int($0.rounded()))%" }) {
                        Text(label)
                            .font(.caption.monospacedDigit().weight(.bold))
                            .foregroundStyle(Theme.ok)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 3)
                            .background(Theme.bg.opacity(0.85), in: Capsule())
                            .padding(6)
                    }
                }
                .overlay(alignment: .bottomTrailing) {
                    Button {
                        discover.addToMix(media)
                    } label: {
                        Image(systemName: "plus")
                            .font(.caption.weight(.bold))
                            .frame(width: 28, height: 28)
                            .background(Theme.bg.opacity(0.85), in: Circle())
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(Theme.text)
                    .padding(6)
                    .accessibilityLabel("Add to mix")
                }
            Text(media.displayTitle(romaji: filters.romajiTitles))
                .font(.footnote.weight(.semibold))
                .foregroundStyle(Theme.text)
                .lineLimit(2)
            Text(media.displayMeta)
                .font(.caption2)
                .foregroundStyle(Theme.muted)
                .lineLimit(1)
        }
        .contentShape(Rectangle())
        .onTapGesture { discover.loadRecommendations(media.id) }
        .contextMenu {
            Button { discover.addToMix(media) } label: { Label("Add to mix", systemImage: "plus") }
            ForEach(media.links ?? [], id: \.self) { link in
                if let url = URL(string: link.url) {
                    Link(destination: url) { Label(link.label, systemImage: "arrow.up.right.square") }
                }
            }
        }
    }
}

/// The seed: cover, genres and tags (tap to require), synopsis and links.
struct SeedCard: View {
    @Environment(Filters.self) private var filters
    @Environment(DiscoverModel.self) private var discover
    let media: Media

    /// Matches the web seed card, which shows the ten best-ranked tags.
    private let maxTags = 10

    var body: some View {
        @Bindable var discover = discover
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 14) {
                Cover(url: media.coverURL)
                    .frame(width: 96, height: 144)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                VStack(alignment: .leading, spacing: 6) {
                    Text("SEED")
                        .font(.caption2.weight(.bold))
                        .tracking(1.2)
                        .foregroundStyle(Theme.accentText)
                    Text(media.displayTitle(romaji: filters.romajiTitles))
                        .font(.title3.weight(.bold))
                        .foregroundStyle(Theme.text)
                    Text(media.displayMeta)
                        .font(.footnote)
                        .foregroundStyle(Theme.muted)
                    Button("+ Add to mix") { discover.addToMix(media) }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .padding(.top, 4)
                }
            }
            let genres = media.genres ?? []
            let tags = Array((media.tags ?? []).prefix(maxTags))
            if !genres.isEmpty || !tags.isEmpty {
                FlowLayout(spacing: 6) {
                    ForEach(genres, id: \.self) { genre in
                        Button { discover.require(genre: genre) } label: { Chip(text: genre) }
                            .buttonStyle(.plain)
                    }
                    ForEach(tags, id: \.self) { tag in
                        Button { discover.require(tag: tag) } label: { Chip(text: tag, foreground: Theme.muted) }
                            .buttonStyle(.plain)
                    }
                }
            }
            if let synopsis = media.description?.trimmed, !synopsis.isEmpty {
                Text(synopsis)
                    .font(.footnote)
                    .foregroundStyle(Theme.text.opacity(0.85))
                    .lineLimit(discover.expanded ? nil : 4)
                if synopsis.count > 200 {
                    Button(discover.expanded ? "Read less" : "Read more") {
                        discover.expanded.toggle()
                    }
                    .font(.footnote.weight(.semibold))
                }
            }
            if let links = media.links, !links.isEmpty {
                FlowLayout(spacing: 6) {
                    ForEach(links, id: \.self) { link in
                        if let url = URL(string: link.url) {
                            Link(destination: url) {
                                Chip(text: link.label, foreground: Theme.accentText)
                            }
                        }
                    }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            LinearGradient(colors: [Theme.seedFrom, Theme.seedTo], startPoint: .topLeading, endPoint: .bottomTrailing),
            in: RoundedRectangle(cornerRadius: 16)
        )
    }
}

/// Adaptations, sequels and side stories: relations, not recommendations.
struct RelatedRow: View {
    @Environment(Filters.self) private var filters
    @Environment(DiscoverModel.self) private var discover
    let items: [RelatedItem]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(items, id: \.self) { item in
                    Button {
                        discover.loadRecommendations(item.media.id)
                    } label: {
                        Chip(text: "\(item.relationType.lowercased().replacingOccurrences(of: "_", with: " ")): \(item.media.displayTitle(romaji: filters.romajiTitles))")
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}
