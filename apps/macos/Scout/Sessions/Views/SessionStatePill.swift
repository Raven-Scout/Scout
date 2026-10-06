import SwiftUI

/// State colours from the design system (spec §6.2). Parked is a shade darker
/// while the session's process is still open.
enum SessionStateStyle {
    static func color(_ state: AgentSessionState, isOpen: Bool = false) -> Color {
        switch state {
        case .needsYou: return DS.Priority.urgent
        case .running:  return DS.Status.ok
        case .waiting:  return DS.Priority.todo
        case .parked:   return isOpen ? DS.Ink.p3 : DS.Ink.p4
        case .stale:    return DS.Priority.done.opacity(0.6)
        case .done:     return DS.Priority.done
        }
    }
}

struct SessionStateDot: View {
    let state: AgentSessionState
    var isOpen = false

    var body: some View {
        Circle()
            .fill(SessionStateStyle.color(state, isOpen: isOpen))
            .frame(width: 8, height: 8)
            .accessibilityLabel(state.label)
    }
}

struct SessionStatePill: View {
    let state: AgentSessionState
    var isOpen = false

    var body: some View {
        HStack(spacing: 5) {
            SessionStateDot(state: state, isOpen: isOpen)
            Text(state.label)
                .font(DS.sans(11, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(Capsule().fill(SessionStateStyle.color(state, isOpen: isOpen).opacity(0.14)))
    }
}
