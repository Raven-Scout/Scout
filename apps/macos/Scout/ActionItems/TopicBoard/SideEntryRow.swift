import SwiftUI

/// An Inbox or Loose ends entry with a × that closes it. Edit ▸ Undo can
/// usually bring it back while the app stays open (only when the engine
/// reports the change it made). The × is always drawn, faint until the row
/// is hovered, so it can be found without hovering (audit #45).
struct SideEntryRow: View {
    static let closeHelp = "Close: outdated or not relevant. You can usually undo this with Edit ▸ Undo while Scout stays open."

    let entry: SideEntry
    /// `nil` hides the ×.
    let onClose: (() -> Void)?

    @State private var hovering = false

    var body: some View {
        HStack(alignment: .top, spacing: 6) {
            VStack(alignment: .leading, spacing: 1) {
                if let source = entry.source {
                    Text(source)
                        .font(DS.font(.caption))
                        .foregroundStyle(DS.Ink.p3)
                }
                if let url = entry.url {
                    Button(entry.text) { NSWorkspace.shared.open(url) }
                        .buttonStyle(.plainHit) // ds-guard: hit area — the entry's own text opens its link
                        .help(url.absoluteString)
                } else {
                    Text(entry.text)
                }
            }
            .font(DS.font(.body))
            .foregroundStyle(DS.Ink.p2)
            .frame(maxWidth: .infinity, alignment: .leading)
            if let onClose {
                Button(action: onClose) {
                    Image(systemName: "xmark")
                        .font(DS.font(.caption2, weight: .semibold))
                        .frame(width: 22, height: 22)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plainHit)
                .foregroundStyle(hovering ? DS.Ink.p2 : DS.Ink.p4)
                .help(Self.closeHelp)
                .accessibilityLabel("Close \(entry.text)")
            }
        }
        .onKeyWindowHover { hovering = $0 }
    }
}
