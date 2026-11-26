import SwiftUI

/// A per-file item's lifecycle status as a design-system pill, toned like
/// `ProposalStatusPill`.
struct ItemStatusPill: View {
    let status: ItemStatus

    /// Longest label the pill shows. An unrecognized `status:` is free text —
    /// one was a 250-character comment — and a pill is `fixedSize`, so an
    /// unbounded label widened its card past the window.
    static let maxLabelLength = 24

    nonisolated static func label(for status: ItemStatus) -> String {
        let name = status.displayName
        guard name.count > maxLabelLength else { return name }
        return name.prefix(maxLabelLength - 1).trimmingCharacters(in: .whitespaces) + "…"
    }

    var body: some View {
        DSPill(Self.label(for: status), tone: Self.tone(status))
            .help(status.displayName)
            .accessibilityLabel(status.displayName)
    }

    nonisolated static func tone(_ status: ItemStatus) -> DS.PillTone {
        switch status {
        case .open:       return .accent
        case .inProgress: return .info
        case .done:       return .ok
        case .dropped:    return .err  // terminal "decided against", like Proposals' rejected
        case .unknown:    return .neutral
        }
    }
}
