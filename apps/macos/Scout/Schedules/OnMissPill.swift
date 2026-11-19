import SwiftUI

/// On-miss policy pill: Skip is quiet, Fire is a warning (it runs late),
/// Collapse is the accent (it runs once for the missed set).
struct OnMissPill: View {
    let policy: OnMissPolicy

    var body: some View {
        DSPill(policy.displayName, tone: Self.tone(policy))
            .help(policy.explanation)
            .accessibilityLabel("If the Mac was asleep: \(policy.displayName)")
    }

    nonisolated static func tone(_ policy: OnMissPolicy) -> DS.PillTone {
        switch policy {
        case .skip:     return .neutral
        case .fire:     return .warn
        case .collapse: return .accent
        }
    }
}
