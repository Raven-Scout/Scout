import SwiftUI

/// Small color-coded capsule for a reply draft's lifecycle status. Part of the
/// editorial chip family (matched-chroma hues, hairline-soft fills), mirroring
/// ``ProposalStatusPill``.
struct DraftStatusPill: View {
    let status: DraftStatus

    var body: some View {
        Text(status.displayName.uppercased())
            .font(DS.sans(10, weight: .semibold))
            .tracking(0.06 * 10)
            .foregroundStyle(tint)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Capsule().fill(tint.opacity(0.14)))
            .overlay(Capsule().strokeBorder(tint.opacity(0.35), lineWidth: 0.5))
            .fixedSize()
    }

    private var tint: Color {
        switch status {
        case .draft:     return DS.Priority.todo
        case .sent:      return DS.Status.ok
        case .dismissed: return DS.Priority.done
        case .unknown:   return DS.Ink.p3
        }
    }
}
