import SwiftUI

/// Titles added from any card; recommendations blend all of them.
struct MixView: View {
    @Environment(AppModel.self) private var app
    @Environment(Filters.self) private var filters
    @Environment(DiscoverModel.self) private var discover

    var body: some View {
        @Bindable var app = app
        NavigationStack {
            List {
                Section {
                    if app.mix.isEmpty {
                        Text("No titles yet. Tap + on any result to add one.")
                            .foregroundStyle(Theme.muted)
                    }
                    ForEach(app.mix) { media in
                        HStack(spacing: 12) {
                            Cover(url: media.coverURL)
                                .frame(width: 44, height: 66)
                                .clipShape(RoundedRectangle(cornerRadius: 6))
                            VStack(alignment: .leading, spacing: 3) {
                                Text(media.displayTitle(romaji: filters.romajiTitles))
                                    .font(.subheadline.weight(.semibold))
                                    .lineLimit(2)
                                Text(media.displayMeta)
                                    .font(.caption)
                                    .foregroundStyle(Theme.muted)
                            }
                            Spacer()
                            Button {
                                app.mix.removeAll { $0.id == media.id }
                            } label: {
                                Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.dim)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Remove")
                        }
                    }
                    .onDelete { app.mix.remove(atOffsets: $0) }
                } header: {
                    Text(app.mix.isEmpty ? "Mix" : "Mix · \(app.mix.count) / \(AppModel.mixMax)")
                } footer: {
                    Text("Titles added from any card. Recommendations blend all of them.")
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.bg)
            .navigationTitle("Mix")
            .navigationBarTitleDisplayMode(.inline)
            .safeAreaInset(edge: .bottom) {
                Button {
                    app.tab = .discover
                    discover.loadMix()
                } label: {
                    Text("Recommend from mix").frame(maxWidth: .infinity, minHeight: 32)
                }
                .buttonStyle(.borderedProminent)
                .disabled(app.mix.isEmpty)
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
            }
        }
    }
}
