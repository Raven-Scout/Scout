import SwiftUI

/// Small color-coded capsule for a proposal's lifecycle status. Reads as part
/// of the editorial chip family (matched-chroma hues, hairline-soft fills)
/// rather than a stoplight.
struct ProposalStatusPill: View {
    let status: ProposalStatus

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
        case .proposed:  return DS.Priority.todo
        case .pending:   return DS.SlotType.consolidation
        case .approved:  return DS.Status.ok
        case .rejected:  return DS.Status.err
        case .applied:   return DS.Priority.watch
        case .unknown:   return DS.Ink.p3
        }
    }
}
