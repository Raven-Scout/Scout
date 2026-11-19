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

    @Test("Action Items keeps the current card's place across a reparse")
    func cursorReconcile() {
        let a = UUID(), b = UUID(), c = UUID(), b2 = UUID(), c2 = UUID()
        // Still drawn: stays.
        #expect(ActionItemsKeyboardContext.reconcile(cursor: b, previousIDs: [a, b, c], ids: [a, b, c]) == b)
        // Renumbered by a write: the row now at its place.
        #expect(ActionItemsKeyboardContext.reconcile(cursor: b, previousIDs: [a, b, c], ids: [a, b2, c2]) == b2)
        // Was last and the list shrank: the new last row.
        #expect(ActionItemsKeyboardContext.reconcile(cursor: c, previousIDs: [a, b, c], ids: [a]) == a)
        #expect(ActionItemsKeyboardContext.reconcile(cursor: c, previousIDs: [a, b, c], ids: []) == nil)
        #expect(ActionItemsKeyboardContext.reconcile(cursor: nil, previousIDs: [a], ids: [a]) == nil)
    }

    @Test("Schedules walks the new draft first, then the slots as drawn")
    func schedulesOrder() {
        let slots = ["morning", "evening"].map { SchedulesView.makeNewDraftSlot(key: $0) }
        let draft = SchedulesView.makeNewDraftSlot(key: "new-slot")
        #expect(SchedulesMasterTable.keyboardOrder(slots: slots, newDraftSlot: nil) == ["morning", "evening"])
        #expect(SchedulesMasterTable.keyboardOrder(slots: slots, newDraftSlot: draft) == ["new-slot", "morning", "evening"])
    }
}
