import SwiftUI

/// The Action Items List's keyboard model (UI audit #50). One card at a time
/// is keyboard-current: ↑ ↓ Home End move it, Return or Space opens and closes
/// it, ⌘↵ marks it done, Esc lets go of it, and a click on a card makes it
/// current. Every card also has a context menu with the same actions.
///
/// It is separate from Select mode's copy selection, which can hold many cards.
struct ActionItemsKeyboardContext: Equatable {
    /// The keyboard-current card.
    var cursorID: UUID?
    /// False while a comment editor has focus: ⌘↵ is then its Send or Save,
    /// so Done doesn't bind it too.
    var commitKeyFree = true
    /// The last Return or Space; the card it names opens or closes.
    var toggleRequest: ToggleRequest?
    /// Makes a card current (a click on it).
    var setCursor: StableAction<UUID>?

    struct ToggleRequest: Equatable {
        let id: UUID
        /// Tells two presses on the same card apart.
        let serial: Int
    }

    /// The key Done binds on `id`'s card: ⌘↵ on the current card while no
    /// comment editor wants it, else none (and no keycap).
    func doneShortcut(for id: UUID) -> KeyboardShortcut? {
        cursorID == id && commitKeyFree ? AppShortcut.commit : nil
    }

    /// The cards the arrow keys walk: the built rows of the task sections, in
    /// the order drawn. Sub-tasks are left out; they have no actions of their
    /// own.
    static func navigableIDs(in sections: [ActionSection], windows: [UUID: TaskWindow]) -> [UUID] {
        ActionItemsLayout.selectableRows(in: sections, windows: windows)
            .filter { $0.indentLevel == 0 }
            .map(\.id)
    }

    /// Where the current card goes after the list changes under it. A write
    /// reparses the day and can renumber every row, so a card that's gone
    /// hands over to whatever now sits at its old position.
    static func reconcile(cursor: UUID?, previousIDs: [UUID], ids: [UUID]) -> UUID? {
        guard let cursor else { return nil }
        if ids.contains(cursor) { return cursor }
        guard let old = previousIDs.firstIndex(of: cursor), !ids.isEmpty else { return nil }
        return ids[min(old, ids.count - 1)]
    }
}

private struct ActionItemsKeyboardKey: EnvironmentKey {
    static let defaultValue = ActionItemsKeyboardContext()
}

extension EnvironmentValues {
    var actionItemsKeyboard: ActionItemsKeyboardContext {
        get { self[ActionItemsKeyboardKey.self] }
        set { self[ActionItemsKeyboardKey.self] = newValue }
    }
}
