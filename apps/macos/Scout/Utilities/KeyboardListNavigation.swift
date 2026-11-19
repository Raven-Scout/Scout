import SwiftUI

/// Arrow-key movement through a list drawn as a stack of rows (Action Items,
/// Schedules, the KB tree), where a `List(selection:)` doesn't fit the page's
/// design. Pure, so each page's order and edges are tested once here.
nonisolated enum KeyboardListNavigation {
    enum Move: Equatable { case previous, next, first, last }

    /// The item after a move. From nothing, ↓ and Home pick the first item and
    /// ↑ and End the last; at either end the current item stays. An item that
    /// is no longer in the list moves as from nothing.
    static func target<ID: Equatable>(from current: ID?, in ids: [ID], move: Move) -> ID? {
        guard !ids.isEmpty else { return nil }
        let index = current.flatMap { ids.firstIndex(of: $0) }
        switch move {
        case .first: return ids.first
        case .last:  return ids.last
        case .next:
            guard let index else { return ids.first }
            return ids[min(index + 1, ids.count - 1)]
        case .previous:
            guard let index else { return ids.last }
            return ids[max(index - 1, 0)]
        }
    }

    /// The move a key press asks for, or nil for any other key. Only bare
    /// keys: ⌘↑ and friends stay with the scroll view and text fields.
    static func move(for key: KeyEquivalent, modifiers: EventModifiers) -> Move? {
        guard modifiers.isDisjoint(with: [.command, .option, .control, .shift]) else { return nil }
        switch key {
        case .upArrow:   return .previous
        case .downArrow: return .next
        case .home:      return .first
        case .end:       return .last
        default:         return nil
        }
    }
}

extension View {
    /// Moves `selection` through `ids` with ↑ ↓ Home End while this view or a
    /// row inside it has keyboard focus, then calls `onMove` with the new
    /// item (to scroll it into view, say).
    func keyboardListNavigation<ID: Equatable>(
        ids: @escaping () -> [ID],
        selection: Binding<ID?>,
        onMove: @escaping (ID) -> Void = { _ in }
    ) -> some View {
        onKeyPress(keys: [.upArrow, .downArrow, .home, .end], phases: [.down, .repeat]) { press in
            guard let move = KeyboardListNavigation.move(for: press.key, modifiers: press.modifiers),
                  let next = KeyboardListNavigation.target(from: selection.wrappedValue, in: ids(), move: move)
            else { return .ignored }
            selection.wrappedValue = next
            onMove(next)
            return .handled
        }
    }
}
