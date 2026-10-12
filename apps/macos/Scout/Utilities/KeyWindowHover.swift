import SwiftUI

/// `.onHover` for hover affordances: reports `true` only while the pointer
/// is inside the view AND its window is key (Raven-Scout/Scout#294). AppKit
/// spends the first click on a non-key window activating it, so a control
/// revealed there (or a pointing-hand cursor over one) advertises a click
/// that never lands. Background windows show no rollover chrome, like the
/// rest of macOS. Only changes are reported; the cursor goes through
/// `keyWindowPointingHand`, which keeps its push/pop balanced.
struct KeyWindowHover: ViewModifier {
    let action: (Bool) -> Void

    @Environment(\.controlActiveState) private var state
    @State private var pointerInside = false
    @State private var tracker = Tracker()

    func body(content: Content) -> some View {
        content
            .onHover { inside in
                pointerInside = inside
                report(pointerInside: inside)
            }
            .onChange(of: state) { report(pointerInside: pointerInside) }
    }

    private func report(pointerInside: Bool) {
        if let revealed = tracker.update(pointerInside: pointerInside, state: state) { action(revealed) }
    }

    static func reveals(pointerInside: Bool, state: ControlActiveState) -> Bool {
        pointerInside && state == .key
    }

    /// The last value reported; `update` returns the new one only when it changes.
    struct Tracker {
        private var revealed = false

        mutating func update(pointerInside: Bool, state: ControlActiveState) -> Bool? {
            let now = KeyWindowHover.reveals(pointerInside: pointerInside, state: state)
            guard now != revealed else { return nil }
            revealed = now
            return now
        }
    }
}

/// The pointing-hand cursor over a clickable view, in the key window only
/// and only while `enabled` (e.g. not while a write is in flight). It pops
/// exactly what it pushed — also when `enabled` flips mid-hover or the view
/// goes away under the pointer — so it never unwinds another view's cursor.
struct KeyWindowPointingHand: ViewModifier {
    let enabled: Bool

    @State private var hovered = false
    @State private var pushed = false

    func body(content: Content) -> some View {
        content
            .onKeyWindowHover { inside in
                hovered = inside
                sync(wanted: inside && enabled)
            }
            .onChange(of: enabled) { sync(wanted: hovered && enabled) }
            .onDisappear { sync(wanted: false) }
    }

    private func sync(wanted: Bool) {
        switch Self.change(pushed: pushed, wanted: wanted) {
        case .push?: NSCursor.pointingHand.push(); pushed = true
        case .pop?:  NSCursor.pop(); pushed = false
        case nil:    break
        }
    }

    enum Change { case push, pop }

    static func change(pushed: Bool, wanted: Bool) -> Change? {
        guard pushed != wanted else { return nil }
        return wanted ? .push : .pop
    }
}

extension View {
    /// `.onHover`, but only while the window is key. Use it for anything
    /// that reveals a control (Raven-Scout/Scout#294).
    func onKeyWindowHover(perform action: @escaping (Bool) -> Void) -> some View {
        modifier(KeyWindowHover(action: action))
    }

    /// The pointing-hand cursor while hovered in the key window and `enabled`.
    func keyWindowPointingHand(enabled: Bool = true) -> some View {
        modifier(KeyWindowPointingHand(enabled: enabled))
    }
}
