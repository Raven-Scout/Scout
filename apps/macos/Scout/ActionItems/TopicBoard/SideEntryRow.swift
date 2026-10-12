import SwiftUI

/// An Inbox or Loose ends entry with a × that closes it for good.
struct SideEntryRow: View {
    let entry: SideEntry
    /// `nil` hides the ×.
    let onClose: (() -> Void)?

    @State private var hovering = false

    var body: some View {
        HStack(alignment: .top, spacing: 6) {
            VStack(alignment: .leading, spacing: 1) {
                if let source = entry.source {
                    Text(source)
                        .font(DS.sans(11.5))
                        .foregroundStyle(DS.Ink.p3)
                }
                if let url = entry.url {
                    Button(entry.text) { NSWorkspace.shared.open(url) }
                        .buttonStyle(.plainHit)
                        .help(url.absoluteString)
                } else {
                    Text(entry.text)
                }
            }
            .font(DS.sans(13))
            .foregroundStyle(DS.Ink.p2)
            .frame(maxWidth: .infinity, alignment: .leading)
            if let onClose {
                Button(action: onClose) {
                    Image(systemName: "xmark")
                        .font(.system(size: 10, weight: .semibold))
                        .frame(width: 22, height: 22)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(DS.Ink.p3)
                .opacity(hovering ? 1 : 0)
                .help("Close: outdated or not relevant")
                .accessibilityLabel("Close \(entry.text)")
            }
        }
        .onHover { hovering = $0 }
    }
}
