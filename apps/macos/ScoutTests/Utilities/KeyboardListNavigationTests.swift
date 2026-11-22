import Foundation
import SwiftUI
import Testing
@testable import Scout

@Suite("Arrow-key list navigation")
struct KeyboardListNavigationTests {
    let ids = ["a", "b", "c"]

    @Test("↓ and ↑ step and stop at the ends")
    func steps() {
        #expect(KeyboardListNavigation.target(from: "a", in: ids, move: .next) == "b")
        #expect(KeyboardListNavigation.target(from: "c", in: ids, move: .next) == "c")
        #expect(KeyboardListNavigation.target(from: "b", in: ids, move: .previous) == "a")
        #expect(KeyboardListNavigation.target(from: "a", in: ids, move: .previous) == "a")
        #expect(KeyboardListNavigation.target(from: "b", in: ids, move: .first) == "a")
        #expect(KeyboardListNavigation.target(from: "b", in: ids, move: .last) == "c")
    }

    @Test("from nothing, or from a row that's gone, ↓ starts at the top and ↑ at the bottom")
    func fromNothing() {
        #expect(KeyboardListNavigation.target(from: nil, in: ids, move: .next) == "a")
        #expect(KeyboardListNavigation.target(from: nil, in: ids, move: .previous) == "c")
        #expect(KeyboardListNavigation.target(from: "gone", in: ids, move: .next) == "a")
        #expect(KeyboardListNavigation.target(from: "a", in: [String](), move: .next) == nil)
    }

    @Test("only bare arrows, Home and End move")
    func keys() {
        #expect(KeyboardListNavigation.move(for: .downArrow, modifiers: []) == .next)
        #expect(KeyboardListNavigation.move(for: .upArrow, modifiers: []) == .previous)
        #expect(KeyboardListNavigation.move(for: .home, modifiers: []) == .first)
        #expect(KeyboardListNavigation.move(for: .end, modifiers: []) == .last)
        #expect(KeyboardListNavigation.move(for: .downArrow, modifiers: .command) == nil)
        #expect(KeyboardListNavigation.move(for: .downArrow, modifiers: .shift) == nil)
        #expect(KeyboardListNavigation.move(for: .return, modifiers: []) == nil)
    }

    typealias Row = ActionItemsKeyboardContext.Row

    @Test("Action Items follows the current card's task across a reparse")
    func cursorFollowsTask() {
        let a = Row(id: UUID(), shortPrefix: "OPS1", subject: "first")
        let b = Row(id: UUID(), shortPrefix: nil, subject: "second")
        let c = Row(id: UUID(), shortPrefix: "OPS3", subject: "third")
        // A scheduled run adds an item at the top: every id moves. The cursor
        // stays on its own task, not on whatever took its old position.
        let added = Row(id: UUID(), shortPrefix: "OPS9", subject: "new")
        let b2 = Row(id: UUID(), shortPrefix: nil, subject: "second")
        let c2 = Row(id: UUID(), shortPrefix: "OPS3", subject: "third")
        #expect(ActionItemsKeyboardContext.reconcile(cursor: b.id, previous: [a, b, c], rows: [added, a, b2, c2]) == b2.id)
        #expect(ActionItemsKeyboardContext.reconcile(cursor: c.id, previous: [a, b, c], rows: [added, a, b2, c2]) == c2.id)
        // A retitled task keeps its [#TAG].
        let c3 = Row(id: UUID(), shortPrefix: "OPS3", subject: "third, reworded")
        #expect(ActionItemsKeyboardContext.reconcile(cursor: c.id, previous: [a, b, c], rows: [a, b, c3]) == c3.id)
        // Unchanged: stays.
        #expect(ActionItemsKeyboardContext.reconcile(cursor: b.id, previous: [a, b, c], rows: [a, b, c]) == b.id)
    }

    @Test("two untagged cards with one subject: the cursor stays on its own")
    func repeatedSubject() {
        let a = Row(id: UUID(), shortPrefix: nil, subject: "Follow up")
        let x = Row(id: UUID(), shortPrefix: nil, subject: "other")
        let b = Row(id: UUID(), shortPrefix: nil, subject: "Follow up")
        // A filter change or a write elsewhere: the cursor's card is still drawn.
        #expect(ActionItemsKeyboardContext.reconcile(cursor: b.id, previous: [a, x, b], rows: [a, x, b]) == b.id)
        #expect(ActionItemsKeyboardContext.reconcile(cursor: b.id, previous: [a, x, b], rows: [x, b]) == b.id)
        // Renumbered by an insert at the top: the nearer "Follow up", not the first.
        let added = Row(id: UUID(), shortPrefix: nil, subject: "new")
        let a2 = Row(id: UUID(), shortPrefix: nil, subject: "Follow up")
        let x2 = Row(id: UUID(), shortPrefix: nil, subject: "other")
        let b2 = Row(id: UUID(), shortPrefix: nil, subject: "Follow up")
        #expect(ActionItemsKeyboardContext.reconcile(cursor: b.id, previous: [a, x, b], rows: [added, a2, x2, b2]) == b2.id)
    }

    @Test("a card that's really gone hands over to its old position")
    func cursorGone() {
        let a = Row(id: UUID(), shortPrefix: nil, subject: "first")
        let b = Row(id: UUID(), shortPrefix: nil, subject: "second")
        let c = Row(id: UUID(), shortPrefix: nil, subject: "third")
        let c2 = Row(id: UUID(), shortPrefix: nil, subject: "third")
        // b marked done: it leaves the list, and the next card takes its place.
        #expect(ActionItemsKeyboardContext.reconcile(cursor: b.id, previous: [a, b, c], rows: [a, c2]) == c2.id)
        // b snoozed: it moves to Snoozed, and the cursor doesn't follow it there.
        let bSnoozed = Row(id: UUID(), shortPrefix: nil, subject: "second", snoozed: true)
        #expect(ActionItemsKeyboardContext.reconcile(cursor: b.id, previous: [a, b, c], rows: [a, c2, bSnoozed]) == c2.id)
        // Was last and the list shrank: the new last row.
        #expect(ActionItemsKeyboardContext.reconcile(cursor: c.id, previous: [a, b, c], rows: [a]) == a.id)
        #expect(ActionItemsKeyboardContext.reconcile(cursor: c.id, previous: [a, b, c], rows: []) == nil)
        #expect(ActionItemsKeyboardContext.reconcile(cursor: nil, previous: [a], rows: [a]) == nil)
    }

    @Test("the list's keys act only while the list itself has focus")
    func listOwnsKeys() {
        #expect(ActionItemsKeyboardContext.listOwnsKeys(listFocused: true, commentEditorFocused: false))
        // Typing Return or Space into a comment must not collapse its card.
        #expect(!ActionItemsKeyboardContext.listOwnsKeys(listFocused: true, commentEditorFocused: true))
        #expect(!ActionItemsKeyboardContext.listOwnsKeys(listFocused: false, commentEditorFocused: false))
    }

    @Test("Schedules walks the new draft first, then the slots as drawn")
    func schedulesOrder() {
        let slots = ["morning", "evening"].map { SchedulesView.makeNewDraftSlot(key: $0) }
        let draft = SchedulesView.makeNewDraftSlot(key: "new-slot")
        #expect(SchedulesMasterTable.keyboardOrder(slots: slots, newDraftSlot: nil) == ["morning", "evening"])
        #expect(SchedulesMasterTable.keyboardOrder(slots: slots, newDraftSlot: draft) == ["new-slot", "morning", "evening"])
    }
}
