import AppKit
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
    /// The card the focus ring marks while the list has the keys: the current
    /// one, else the first, where ↓ starts (`KeyboardListNavigation.ringed`).
    var ringID: UUID?
    /// False while a comment editor has focus (⌘↵ is then its Send or Save,
    /// so Done doesn't bind it too) or the search field does.
    var commitKeyFree = true
    /// The last Return or Space; the card it names opens or closes.
    var toggleRequest: ToggleRequest?
    /// Makes a card current and gives the list keyboard focus, so the arrows
    /// work next: a click anywhere on the card, unless the click left the keys
    /// with text (`clickTakesListFocus`). A click that only made the card
    /// current left the list without focus when it came from elsewhere, the
    /// sidebar say: gold, no ring, and ↓ did nothing (UI audit T4).
    var focusCard: StableAction<UUID>?

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

    /// Whether the list's own key handlers (↑ ↓ Home End, Return, Space, Esc)
    /// may act. Only while the list itself has focus: the handlers sit on an
    /// ancestor of every comment editor and button, and Return or Space typed
    /// into a comment would otherwise collapse its card and lose the draft.
    static func listOwnsKeys(listFocused: Bool, commentEditorFocused: Bool) -> Bool {
        listFocused && !commentEditorFocused
    }

    /// Whether a click on a card may take the list's keyboard focus, given
    /// what holds it once the click is done. Not from text the click left the
    /// keys with: a comment editor it clicked into (an editable text view, not
    /// a field's shared editor, which is the search field's), or text it just
    /// selected, which ⌘C must still copy. A field elsewhere (search) gives
    /// way: the click was on the list.
    static func clickTakesListFocus(firstResponder: NSResponder?) -> Bool {
        if let text = firstResponder as? NSTextView {
            if text.isEditable && !text.isFieldEditor { return false }
            if text.selectedRange().length > 0 { return false }
            return true
        }
        if let view = firstResponder as? NSView, view.accessibilitySelectedTextRange().length > 0 {
            return false
        }
        return true
    }

    /// A card the arrow keys walk, with what identifies its task across a
    /// reparse.
    struct Row: Equatable {
        let id: UUID
        let shortPrefix: String?
        let subject: String
        /// A snoozed card is one the user just put away, not one to follow.
        let snoozed: Bool

        init(id: UUID, shortPrefix: String?, subject: String, snoozed: Bool = false) {
            self.id = id
            self.shortPrefix = shortPrefix
            self.subject = subject
            self.snoozed = snoozed
        }

        init(_ task: ActionTask) {
            self.init(id: task.id, shortPrefix: task.shortPrefix, subject: task.matchableSubject,
                      snoozed: task.snoozedUntil != nil)
        }
    }

    /// The cards the arrow keys walk: the built rows of the task sections, in
    /// the order drawn. Sub-tasks are left out; they have no actions of their
    /// own.
    static func navigableRows(in sections: [ActionSection], windows: [UUID: TaskWindow]) -> [Row] {
        ActionItemsLayout.selectableRows(in: sections, windows: windows)
            .filter { $0.indentLevel == 0 }
            .map(Row.init)
    }

    /// Where the current card goes after the list changes under it. A card
    /// still drawn keeps the cursor. Task ids encode a row's position, so a
    /// write anywhere above (a scheduled run adding an item, say) renumbers
    /// the card: then follow its task by `[#TAG]`, then by subject. When
    /// untagged cards share a subject, take the one in the same place among
    /// them (the second "Follow up" stays the second), so ⌘↵ never lands on a
    /// neighbour. Only a card that's really gone (done, snoozed, filtered out)
    /// hands over to whatever now sits at its old position.
    static func reconcile(cursor: UUID?, previous: [Row], rows: [Row]) -> UUID? {
        guard let cursor, !rows.isEmpty else { return nil }
        if rows.contains(where: { $0.id == cursor }) { return cursor }
        guard let old = previous.firstIndex(where: { $0.id == cursor }) else { return nil }
        let task = previous[old]
        let candidates = rows.filter { $0.snoozed == task.snoozed }
        if let tag = task.shortPrefix, let same = candidates.first(where: { $0.shortPrefix == tag }) {
            return same.id
        }
        let namesakes = candidates.filter { $0.subject == task.subject }
        if !namesakes.isEmpty {
            let place = previous[..<old].filter { $0.subject == task.subject && $0.snoozed == task.snoozed }.count
            return namesakes[min(place, namesakes.count - 1)].id
        }
        return rows[min(old, rows.count - 1)].id
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
