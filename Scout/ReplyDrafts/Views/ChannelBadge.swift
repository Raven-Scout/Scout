import SwiftUI

/// Small icon+label badge showing which channel a reply is owed on. Reads as a
/// quiet metadata chip next to the status pill.
struct ChannelBadge: View {
    let channel: DraftChannel

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: channel.systemImage)
                .font(.system(size: 9, weight: .semibold))
            Text(channel.displayName.uppercased())
                .font(DS.sans(10, weight: .semibold))
                .tracking(0.06 * 10)
        }
        .foregroundStyle(tint)
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(Capsule().fill(tint.opacity(0.12)))
        .overlay(Capsule().strokeBorder(tint.opacity(0.30), lineWidth: 0.5))
        .fixedSize()
    }

    private var tint: Color {
        switch channel {
        case .email:    return DS.Accent.ink
        case .slack:    return DS.Priority.personal
        case .linear:   return DS.Priority.todo
        case .github:   return DS.Ink.p2
        case .whatsapp: return DS.Status.ok
        case .other:    return DS.Ink.p3
        }
    }
}
