import SwiftUI

/// On-miss policy badge. SKIP / FIRE / COLLAPSE in uppercase, color-tinted
/// per policy:
///   - SKIP     → DS.Ink.p3 background (quiet)
///   - FIRE     → DS.Status.warn background (active)
///   - COLLAPSE → DS.Accent.wash background (deferred)
struct OnMissPill: View {
    let policy: OnMissPolicy

    var body: some View {
        Text(policy.rawValue.uppercased())
            .font(DS.sans(11, weight: .medium))
            .foregroundStyle(foreground)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(background, in: Capsule())
            .accessibilityLabel("On miss: \(policy.rawValue)")
    }

    private var background: Color {
        switch policy {
        case .skip:     return DS.Ink.p4.opacity(0.18)
        case .fire:     return DS.Status.warn.opacity(0.20)
        case .collapse: return DS.Accent.wash
        }
    }

    private var foreground: Color {
        switch policy {
        case .skip:     return DS.Ink.p2
        case .fire:     return DS.Status.warn
        case .collapse: return DS.Accent.ink
        }
    }
}
