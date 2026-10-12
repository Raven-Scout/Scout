import SwiftUI

/// `.onHover` for hover affordances: reports `true` only while the pointer
/// is inside the view AND its window is key (Raven-Scout/Scout#294). AppKit spends the first
/// click on a non-key window activating it, so a control revealed there (or
/// a pointing-hand cursor over one) advertises a click that never lands.
/// Background windows show no rollover chrome, like the rest of macOS.
///
/// Only changes are reported, so a `NSCursor.push()` / `pop()` pair stays
/// balanced when the window loses key while the pointer is inside.
struct KeyWindowHover: ViewModifier {
    let action: (Bool) -> Void

    @Environment(\.controlActiveState) private var state
    @State private var pointerInside = false
    @State private var tracker = Tracker()

    func body(content: Content) -> some View {
        content
            .onHover { inside in
                pointerInside = inside
                report()
            }
            .onChange(of: state) { report() }
    }

    private func report() {
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

extension View {
    /// `.onHover`, but only while the window is key. Use it for anything
    /// that reveals a control or shows the pointing-hand cursor (Raven-Scout/Scout#294).
    func onKeyWindowHover(perform action: @escaping (Bool) -> Void) -> some View {
        modifier(KeyWindowHover(action: action))
    }
}
