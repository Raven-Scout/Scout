import AppKit
import SwiftUI

/// Arrow-key movement through a list drawn as a stack of rows (Action Items,
/// Schedules, the KB tree), where a `List(selection:)` doesn't fit the page's
/// design. Pure, so each page's order and edges are tested once here.
nonisolated enum KeyboardListNavigation {
    enum Move: Equatable { case previous, next, first, last }

    /// The item after a move. From nothing, ↓ and Home pick the first item and
    /// ↑ and End the last; at either end the current item stays. An item that
    /// is no longer in the list moves as from nothing.
    /// The item a focused list rings (`dsCursorRing`): the current one while
    /// it's still listed, else the first, the one ↓ picks from nothing (UI
    /// audit T4: a list with nothing current was a silent Tab stop).
    static func ringed<ID: Equatable>(_ current: ID?, in ids: [ID]) -> ID? {
        if let current, ids.contains(current) { return current }
        return ids.first
    }

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

/// Says a list's new current item aloud while VoiceOver runs. A list takes
/// the keys through a focus sink hidden from accessibility and its items are
/// no Tab stops, so a cursor the arrow keys move is otherwise silent (review
/// of #767).
enum ListCursorAnnouncement {
    static func post(_ label: String?) {
        guard let label, !label.isEmpty, NSWorkspace.shared.isVoiceOverEnabled else { return }
        NSAccessibility.post(
            element: NSApp as Any, notification: .announcementRequested,
            userInfo: [.announcement: label, .priority: NSAccessibilityPriorityLevel.high.rawValue])
    }
}

extension View {
    /// Moves `selection` through `ids` with ↑ ↓ Home End while this view or a
    /// row inside it has keyboard focus, then calls `onMove` with the new
    /// item (to scroll it into view, say). VoiceOver hears `announce`'s name
    /// for the new item. Pass `when:` the list's own focus
    /// (`dsFocusContainer`): an arrow a focused button inside didn't use
    /// reaches this handler too, and must not move a cursor whose ring isn't
    /// drawn (UI audit T4).
    func keyboardListNavigation<ID: Equatable>(
        when active: Bool = true,
        ids: @escaping () -> [ID],
        selection: Binding<ID?>,
        announce: @escaping (ID) -> String? = { _ in nil },
        onMove: @escaping (ID) -> Void = { _ in }
    ) -> some View {
        onKeyPress(keys: [.upArrow, .downArrow, .home, .end], phases: [.down, .repeat]) { press in
            guard active, let move = KeyboardListNavigation.move(for: press.key, modifiers: press.modifiers),
                  let next = KeyboardListNavigation.target(from: selection.wrappedValue, in: ids(), move: move)
            else { return .ignored }
            selection.wrappedValue = next
            ListCursorAnnouncement.post(announce(next))
            onMove(next)
            return .handled
        }
    }
}
