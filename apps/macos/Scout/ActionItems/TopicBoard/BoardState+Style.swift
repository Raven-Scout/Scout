import SwiftUI

extension BoardState {
    var color: Color {
        switch self {
        case .mine: DS.Priority.urgent
        case .doing: DS.SlotType.consolidation
        case .waiting: DS.Priority.todo
        case .blocked: DS.Ink.p3
        case .hold: DS.Priority.done
        case .done: DS.Status.ok
        }
    }

    /// Blocked is drawn as a ring: it isn't anyone's move yet.
    var isRing: Bool { self == .blocked }
}
