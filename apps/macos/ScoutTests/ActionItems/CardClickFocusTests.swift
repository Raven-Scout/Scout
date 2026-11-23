import AppKit
import Testing
@testable import Scout

/// A view with text selected, as a selectable SwiftUI text reports it.
private final class SelectedTextView: NSView {
    var range = NSRange(location: 0, length: 0)
    var role: NSAccessibility.Role = .staticText
    override func accessibilitySelectedTextRange() -> NSRange { range }
    override func accessibilityRole() -> NSAccessibility.Role? { role }
}

/// A click on an Action Items card takes the list's keys unless the click
/// itself left them with text (UI audit T4; review of #767).
@Suite("A click on a card and the keys")
@MainActor
struct CardClickFocusTests {
    private typealias Holder = ActionItemsKeyboardContext.KeyHolder

    private func takes(_ before: Holder?, _ now: Holder, claimed: Bool = false) -> Bool {
        ActionItemsKeyboardContext.clickTakesListFocus(atMouseDown: before, now: now, claimed: claimed)
    }

    @Test("what holds text: a comment editor, or a selection, not the search field's editor or a bare view")
    func holdsText() {
        #expect(!Holder(responder: nil).holdsText)
        #expect(!Holder(responder: NSView()).holdsText)
        #expect(Holder(responder: NSTextView()).holdsText)
        let fieldEditor = NSTextView()
        fieldEditor.isFieldEditor = true
        #expect(!Holder(responder: fieldEditor).holdsText)
        let selectable = NSTextView()
        selectable.isEditable = false
        selectable.string = "Reply to Priya"
        selectable.setSelectedRange(NSRange(location: 0, length: 5))
        #expect(Holder(responder: selectable).holdsText)
        selectable.setSelectedRange(NSRange(location: 2, length: 0))
        #expect(!Holder(responder: selectable).holdsText)
        // A plain view's selected text, as SwiftUI's selectable text has it.
        let view = SelectedTextView()
        #expect(!Holder(responder: view).holdsText)
        view.range = NSRange(location: 3, length: 4)
        #expect(Holder(responder: view).holdsText)
        view.range = NSRange(location: NSNotFound, length: 4)
        #expect(!Holder(responder: view).holdsText)
    }

    @Test("the sidebar, a list or a button holds no text, whatever it reports selected: the click takes the keys")
    func sidebarGivesUpTheKeys() {
        // The sidebar is an outline view with a selected row.
        let sidebar = NSOutlineView()
        #expect(!Holder(responder: sidebar).holdsText)
        #expect(takes(Holder(responder: sidebar, hit: NSView()), Holder(responder: sidebar)))
        for role: NSAccessibility.Role in [.outline, .list, .table, .button, .group] {
            let view = SelectedTextView()
            view.role = role
            view.range = NSRange(location: 0, length: 3)
            #expect(!Holder(responder: view).holdsText, "\(role.rawValue) held text")
            #expect(takes(Holder(responder: view, hit: NSView()), Holder(responder: view)))
        }
        #expect(takes(Holder(responder: NSButton()), Holder(responder: NSButton())))
    }

    @Test("a click lands inside what holds the keys when it hits that view or one inside it")
    func clickInside() {
        let editor = NSTextView()
        let inner = NSView()
        editor.addSubview(inner)
        #expect(Holder(responder: editor, hit: editor).clickInside)
        #expect(Holder(responder: editor, hit: inner).clickInside)
        #expect(!Holder(responder: editor, hit: NSView()).clickInside)
        #expect(!Holder(responder: editor).clickInside)
    }

    @Test("nothing holding text: the click takes the keys")
    func noText() {
        #expect(takes(nil, Holder(responder: nil)))
        #expect(takes(Holder(responder: NSView()), Holder(responder: NSView())))
        let fieldEditor = NSTextView()
        fieldEditor.isFieldEditor = true
        #expect(takes(Holder(responder: fieldEditor), Holder(responder: fieldEditor)))
    }

    @Test("an editor that had the keys before the click, unchanged by it, gives them up")
    func editorElsewhere() {
        let editor = NSTextView()
        editor.string = "draft"
        editor.setSelectedRange(NSRange(location: 5, length: 0))
        let before = Holder(responder: editor, hit: NSView())
        #expect(takes(before, Holder(responder: editor)))
    }

    @Test("text selected before the click, unchanged by it, gives up the keys")
    func selectionElsewhere() {
        let selectable = NSTextView()
        selectable.isEditable = false
        selectable.string = "Reply to Priya"
        selectable.setSelectedRange(NSRange(location: 0, length: 5))
        let before = Holder(responder: selectable, hit: NSView())
        #expect(takes(before, Holder(responder: selectable)))
    }

    @Test("text this click went into, clicked inside, or selected keeps the keys")
    func textOfThisClick() {
        let editor = NSTextView()
        // Into an editor: something else had the keys at mouse-down.
        #expect(!takes(Holder(responder: NSView()), Holder(responder: editor)))
        // Inside the editor that already had them, the caret where it was.
        #expect(!takes(Holder(responder: editor, hit: editor), Holder(responder: editor)))
        // A new selection in text that had the keys.
        let selectable = NSTextView()
        selectable.isEditable = false
        selectable.string = "Reply to Priya"
        selectable.setSelectedRange(NSRange(location: 0, length: 0))
        let before = Holder(responder: selectable, hit: NSView())
        selectable.setSelectedRange(NSRange(location: 0, length: 5))
        #expect(!takes(before, Holder(responder: selectable)))
        // No mouse-down seen: a draft is safer where it is.
        #expect(!takes(nil, Holder(responder: editor)))
    }

    @Test("a control that opens an editor claims the keys")
    func claimed() {
        #expect(!takes(Holder(responder: nil), Holder(responder: nil), claimed: true))
    }

    @Test("CardClickFocus decides in the card's tap: a focus set a turn later is dropped out of the sidebar")
    func decidesInTheTap() {
        let id = UUID()
        let focus = CardClickFocus()
        var cursor: UUID?
        var listFocused = false
        // No claim: the list has the keys before the tap returns.
        focus.mouseDown(in: nil, at: .zero)
        focus.clicked(id, setCursor: { cursor = $0 }, focusList: { listFocused = true })
        #expect(cursor == id, "the card isn't current at once")
        #expect(listFocused, "the list didn't get the keys in the tap")

        // Add comment's action ran first: the editor's control has the keys.
        listFocused = false
        focus.mouseDown(in: nil, at: .zero)
        focus.claimKeys()
        focus.clicked(id, setCursor: { cursor = $0 }, focusList: { listFocused = true })
        #expect(!listFocused, "the list took the keys from the editor's control")

        // Its action ran after the tap: the list has them, and the editor
        // takes them from it a turn later (FocusContainerWindowTests). The
        // claim doesn't outlive its click.
        focus.mouseDown(in: nil, at: .zero)
        focus.clicked(id, setCursor: { cursor = $0 }, focusList: { listFocused = true })
        focus.claimKeys()
        #expect(listFocused)
        listFocused = false
        focus.mouseDown(in: nil, at: .zero)
        focus.clicked(id, setCursor: { cursor = $0 }, focusList: { listFocused = true })
        #expect(listFocused, "a claim carried over to the next click")
    }
}
