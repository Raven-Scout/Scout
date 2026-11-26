import SwiftUI

/// A proposal's lifecycle status as a design-system pill.
struct ProposalStatusPill: View {
    let status: ProposalStatus

    var body: some View {
        DSPill(status.displayName, tone: Self.tone(status))
    }

    nonisolated static func tone(_ status: ProposalStatus) -> DS.PillTone {
        switch status {
        case .proposed:  return .accent
        case .pending:   return .info
        case .approved:  return .ok
        case .rejected:  return .err
        case .applied:   return .ok
        case .closed:    return .neutral
        case .unknown:   return .neutral
        }
    }
}
