import Testing
import SwiftUI
@testable import Scout

/// Raven-Scout/Scout#294: hover-revealed controls and the pointing-hand cursor only show in
/// the key window, where a click reaches them.
@Suite("KeyWindowHover")
struct KeyWindowHoverTests {
    @Test func revealsOnlyWhenThePointerIsInsideTheKeyWindow() {
        #expect(KeyWindowHover.reveals(pointerInside: true, state: .key))
        #expect(!KeyWindowHover.reveals(pointerInside: true, state: .active))
        #expect(!KeyWindowHover.reveals(pointerInside: true, state: .inactive))
        #expect(!KeyWindowHover.reveals(pointerInside: false, state: .key))
    }

    @Test func reportsOnlyChangesSoCursorPushesStayBalanced() {
        var tracker = KeyWindowHover.Tracker()
        #expect(tracker.update(pointerInside: true, state: .inactive) == nil)   // hovering a background window
        #expect(tracker.update(pointerInside: true, state: .key) == true)       // its first click made it key
        #expect(tracker.update(pointerInside: true, state: .key) == nil)
        #expect(tracker.update(pointerInside: true, state: .inactive) == false) // another window took key
        #expect(tracker.update(pointerInside: false, state: .inactive) == nil)
        #expect(tracker.update(pointerInside: false, state: .key) == nil)
        #expect(tracker.update(pointerInside: true, state: .key) == true)
        #expect(tracker.update(pointerInside: false, state: .key) == false)
    }

    @Test func aSheetClosingRevealsUnderARestingPointer() {
        var tracker = KeyWindowHover.Tracker()
        #expect(tracker.update(pointerInside: true, state: .active) == nil)     // a sheet is key
        #expect(tracker.update(pointerInside: true, state: .key) == true)
    }

    @Test func thePointingHandPopsOnlyWhatItPushed() {
        #expect(KeyWindowPointingHand.change(pushed: false, wanted: true) == .push)
        #expect(KeyWindowPointingHand.change(pushed: true, wanted: false) == .pop)
        #expect(KeyWindowPointingHand.change(pushed: false, wanted: false) == nil)  // busy card regains key: no stray pop
        #expect(KeyWindowPointingHand.change(pushed: true, wanted: true) == nil)
    }
}
