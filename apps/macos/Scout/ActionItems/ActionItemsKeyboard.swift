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
    /// with text (`CardClickFocus`, `clickTakesListFocus`). A click that only made the card
    /// current left the list without focus when it came from elsewhere, the
    /// sidebar say: gold, no ring, and ↓ did nothing (UI audit T4).
    var focusCard: StableAction<UUID>?
    /// Makes a card current and gives the list the keys, unconditionally: a
    /// card collapsing while its comment editor has them. The editor is torn
    /// down with the card's detail, and the list only heard it let go once
    /// the preference caught up, a render later, so the keys sat for that
    /// long on the list's sink with no ring.
    var takeKeys: StableAction<UUID>?

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

    /// What holds the keys at one moment of a click, as far as the click rule
    /// needs: who, and the text it has selected.
    struct KeyHolder: Equatable {
        let responder: ObjectIdentifier?
        let selection: NSRange?
        /// A comment editor (an editable text view, not a field's shared
        /// editor, which is the search field's) or text with a selection,
        /// which ⌘C must still copy. Nothing else: the sidebar, a list or a
        /// button gives the keys up to the clicked list (review of #767).
        let holdsText: Bool
        /// Whether the click landed inside it (set at mouse-down only).
        var clickInside = false

        /// The roles of a view other than a text view that can hold selected
        /// text. A list or outline (the sidebar) has a selection of rows, not text.
        static let textRoles: Set<NSAccessibility.Role?> = [.textArea, .textField, .staticText]

        init(responder: NSResponder?, hit: NSView? = nil) {
            self.responder = responder.map(ObjectIdentifier.init)
            if let text = responder as? NSTextView {
                selection = text.selectedRange()
                holdsText = (text.isEditable && !text.isFieldEditor) || text.selectedRange().length > 0
            } else if let view = responder as? NSView, Self.textRoles.contains(view.accessibilityRole()) {
                let range = view.accessibilitySelectedTextRange()
                selection = range
                holdsText = range.location != NSNotFound && range.length > 0
            } else {
                selection = nil
                holdsText = false
            }
            if let hit, let view = responder as? NSView {
                clickInside = hit === view || hit.isDescendant(of: view)
            }
        }
    }

    /// Whether a click on a card may take the list's keyboard focus, from what
    /// held the keys when it began (`atMouseDown`) and once it ended (`now`).
    /// Not when a control the click hit opens an editor (`claimed`), and not
    /// from text this click left the keys with: one it clicked into, inside,
    /// or selected. Text that already had the keys before the click, in
    /// another card's comment editor say, gives way: the click was on the
    /// list (review of #767). So does a field elsewhere, the search.
    static func clickTakesListFocus(atMouseDown: KeyHolder?, now: KeyHolder, claimed: Bool) -> Bool {
        if claimed { return false }
        guard now.holdsText else { return true }
        // No mouse-down seen: keep the keys where they are, a draft is safer.
        guard let before = atMouseDown else { return false }
        if before.clickInside { return false }
        return before.responder == now.responder && before.selection == now.selection
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

/// Gives a click on a card the list's keys (`ActionItemsKeyboardContext.focusCard`).
/// It notes what held the keys at mouse-down and decides in the card's tap,
/// while the click is still being handled: a focus set a turn later is
/// dropped when the keys are in the sidebar, so the list never got them
/// (review of #767). A control that opens an editor (Add comment, Edit
/// comment) claims the keys; if its action runs before the card's tap, the
/// list leaves them alone, and if after, the editor takes them from the list
/// a turn later. Either way the editor ends up with them.
@MainActor
final class CardClickFocus {
    private var monitor: Any?
    private var atMouseDown: ActionItemsKeyboardContext.KeyHolder?
    private weak var window: NSWindow?
    private var claimed = false

    /// Watches this app's mouse-downs while the list is on screen. Returns
    /// every event untouched.
    func start() {
        guard monitor == nil else { return }
        monitor = NSEvent.addLocalMonitorForEvents(matching: .leftMouseDown) { [weak self] event in
            self?.mouseDown(in: event.window, at: event.locationInWindow)
            return event
        }
    }

    func stop() {
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
    }

    /// What holds the keys as a click begins at `point` (window coordinates).
    func mouseDown(in window: NSWindow?, at point: NSPoint) {
        self.window = window
        claimed = false
        let hit = window?.contentView.flatMap { view in view.hitTest(view.convert(point, from: nil)) }
        atMouseDown = .init(responder: window?.firstResponder, hit: hit)
    }

    /// A control the click hit is opening an editor: the keys go there.
    func claimKeys() { claimed = true }

    /// Makes `id` current and gives the list the keys, now, unless the click
    /// left them with text or a control claimed them for an editor.
    func clicked(_ id: UUID, setCursor: (UUID) -> Void, focusList: () -> Void) {
        setCursor(id)
        let now = ActionItemsKeyboardContext.KeyHolder(responder: (window ?? NSApp.keyWindow)?.firstResponder)
        let takes = ActionItemsKeyboardContext.clickTakesListFocus(atMouseDown: atMouseDown, now: now,
                                                                   claimed: claimed)
        claimed = false
        atMouseDown = nil
        if takes { focusList() }
    }
}

private struct ActionItemsKeyboardKey: EnvironmentKey {
    static let defaultValue = ActionItemsKeyboardContext()
}

private struct CardClickFocusKey: EnvironmentKey {
    static var defaultValue: CardClickFocus? { nil }
}

extension EnvironmentValues {
    var actionItemsKeyboard: ActionItemsKeyboardContext {
        get { self[ActionItemsKeyboardKey.self] }
        set { self[ActionItemsKeyboardKey.self] = newValue }
    }

    /// The Action Items list's click-to-focus helper, for a control inside a
    /// card that opens an editor to claim the keys (`claimKeys`).
    var cardClickFocus: CardClickFocus? {
        get { self[CardClickFocusKey.self] }
        set { self[CardClickFocusKey.self] = newValue }
    }
}
