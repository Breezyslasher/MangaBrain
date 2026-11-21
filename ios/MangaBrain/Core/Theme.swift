import SwiftUI

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255)
    }
}

/// The Android app's palette (res/values/colors.xml), so both look alike.
enum Theme {
    static let bg = Color(hex: 0x0E1015)
    static let surface = Color(hex: 0x141821)
    static let panel = Color(hex: 0x1A1E27)
    static let card = Color(hex: 0x202634)
    static let border = Color(hex: 0x2B3245)
    static let text = Color(hex: 0xE6E9F0)
    static let muted = Color(hex: 0x8B93A7)
    static let dim = Color(hex: 0x727A8E)
    static let accent = Color(hex: 0x5B8DEF)
    static let accentBg = Color(hex: 0x1D2B44)
    static let accentText = Color(hex: 0x8FB0F5)
    static let ok = Color(hex: 0x37C9A5)
    static let okBg = Color(hex: 0x1D3B2A)
    static let bad = Color(hex: 0xE05C6E)
    static let badBg = Color(hex: 0x3B1D22)
    static let wSynopsis = Color(hex: 0x5B8DEF)
    static let wTags = Color(hex: 0x37C9A5)
    static let wGenres = Color(hex: 0xE3B341)
    static let wTaste = Color(hex: 0x8B93A7)
    static let seedFrom = Color(hex: 0x222A42)
    static let seedTo = Color(hex: 0x372E4A)
}

/// Lays chips out in rows, wrapping at the available width.
struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0, widest: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0 && x + size.width > width {
                y += rowHeight + spacing
                x = 0
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
            widest = max(widest, x - spacing)
        }
        return CGSize(width: proposal.width ?? widest, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, rowHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > bounds.minX && x + size.width > bounds.maxX {
                y += rowHeight + spacing
                x = bounds.minX
                rowHeight = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}

/// A rounded tag. Genres read brighter than tags, as on the web seed card.
struct Chip: View {
    let text: String
    var foreground: Color = Theme.text
    var background: Color = Theme.panel
    var closable = false

    var body: some View {
        HStack(spacing: 4) {
            Text(text)
            if closable { Image(systemName: "xmark").font(.caption2.weight(.bold)) }
        }
        .font(.footnote)
        .foregroundStyle(foreground)
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(background, in: Capsule())
        .overlay(Capsule().stroke(Theme.border, lineWidth: 1))
    }
}

/// A cover image with a muted placeholder while it loads or when missing.
struct Cover: View {
    let url: URL?

    var body: some View {
        AsyncImage(url: url) { phase in
            if let image = phase.image {
                image.resizable().scaledToFill()
            } else {
                Rectangle().fill(Theme.card)
                    .overlay(Image(systemName: "photo").foregroundStyle(Theme.dim))
            }
        }
    }
}
