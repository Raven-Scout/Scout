import SwiftUI

/// Where a master–detail page's fixed-width detail pane goes at a given page
/// width (UI audit C6). Sessions and Schedules put a 380 pt pane beside their
/// list; at the minimum window that left the list about 500 pt, which cut the
/// Sessions title and the Schedules weekday column. Below `compactBelow` the
/// pane is narrower, and with nothing selected it steps aside entirely.
nonisolated enum DetailPaneLayout: Equatable, Sendable {
    case hidden
    case shown(width: CGFloat, closable: Bool)

    static let compactBelow: CGFloat = 1000
    static let regularWidth: CGFloat = 380
    static let compactWidth: CGFloat = 340

    /// `pageWidth` nil (not laid out yet) keeps the regular pane.
    ///
    /// `hidesWhenEmpty` (Sessions, Schedules) steps the pane aside whenever
    /// nothing is selected, at any width, and lets a shown pane always be
    /// closed: an empty "Pick a session" took about a quarter of the window at
    /// 1280 and 1600 pt, and "Pick a schedule" pushed the 1280 pt table into
    /// cards (UI audit T4).
    static func layout(pageWidth: CGFloat?, hasSelection: Bool, hidesWhenEmpty: Bool = false) -> DetailPaneLayout {
        if hidesWhenEmpty {
            guard hasSelection else { return .hidden }
            let compact = pageWidth.map { $0 < compactBelow } ?? false
            return .shown(width: compact ? compactWidth : regularWidth, closable: true)
        }
        guard let pageWidth, pageWidth < compactBelow else {
            return .shown(width: regularWidth, closable: false)
        }
        return hasSelection ? .shown(width: compactWidth, closable: true) : .hidden
    }
}

/// The close button a compact detail pane shows in its top-trailing corner.
struct DetailPaneCloseButton: View {
    let help: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "xmark")
                .font(DS.font(.caption, weight: .semibold))
                .foregroundStyle(DS.Ink.p3)
                .frame(width: 24, height: 24)
        }
        .buttonStyle(.plainHit)
        .padding(8)
        .help("\(help) (Esc)")
        .accessibilityLabel(help)
    }
}
