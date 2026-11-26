import AppKit

/// Real key and mouse events into one in-process window, and a way to find
/// where to click without an accessibility tree. Nothing reaches other apps.
/// Tests that use it make their window key: run them in `KeyWindowGate`.
@MainActor
enum WindowEvents {
    static let tab = ("\t", UInt16(48))
    static let down = ("\u{F701}", UInt16(125))

    /// Whether Keyboard navigation (System Settings ▸ Keyboard) is on, as
    /// `NSApplication.isFullKeyboardAccessEnabled` reads it, for
    /// `.enabled(if:)`: tests that Tab onto buttons are skipped without it.
    nonisolated static var keyboardNavigationOn: Bool {
        let global = UserDefaults.standard.persistentDomain(forName: UserDefaults.globalDomain)
        return ((global?["AppleKeyboardUIMode"] as? Int) ?? 0) & 2 != 0
    }

    /// Types `text` into the window, one key event pair per character.
    static func type(_ text: String, in window: NSWindow) {
        for character in text { press((String(character), 0), in: window) }
    }

    static func press(_ key: (String, UInt16), in window: NSWindow) {
        for type in [NSEvent.EventType.keyDown, .keyUp] {
            guard let event = NSEvent.keyEvent(
                with: type, location: .zero, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                windowNumber: window.windowNumber, context: nil,
                characters: key.0, charactersIgnoringModifiers: key.0,
                isARepeat: false, keyCode: key.1
            ) else { continue }
            window.sendEvent(event)
        }
    }

    /// A left click at `point`, in the window's coordinates. The mouse-up goes
    /// into the event queue before the mouse-down is sent: a view that tracks
    /// the mouse (a text view selecting) waits for it there in a loop of its
    /// own, which a mouse-up sent after the mouse-down returns never ends.
    /// When no view took it, it comes back out of the queue and goes to the
    /// window directly, where SwiftUI's tap gestures look for it.
    static func click(_ point: NSPoint, in window: NSWindow) {
        func event(_ type: NSEvent.EventType) -> NSEvent? {
            NSEvent.mouseEvent(
                with: type, location: point, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                windowNumber: window.windowNumber, context: nil, eventNumber: 0,
                clickCount: 1, pressure: type == .leftMouseDown ? 1 : 0)
        }
        guard let down = event(.leftMouseDown), let up = event(.leftMouseUp) else { return }
        NSApp.postEvent(up, atStart: false)
        window.sendEvent(down)
        if let queued = NSApp.nextEvent(matching: .leftMouseUp, until: .distantPast, inMode: .default, dequeue: true) {
            window.sendEvent(queued)
        }
    }

    /// Clicks down the column at `x` (points from the window's left), from
    /// `fromTop` points below the content's top edge, every `step` points,
    /// until `hit()` says a click landed. SwiftUI builds no accessibility
    /// tree without an assistive app running, so the tests can't look an
    /// item's frame up; the first click that lands is a real click on the
    /// first item in that column. Returns how far below the top the click
    /// that landed was, or nil when none did.
    @discardableResult
    static func clickDown(x: CGFloat, fromTop: CGFloat, step: CGFloat = 6, in window: NSWindow,
                          until hit: () -> Bool) async throws -> CGFloat? {
        guard let height = window.contentView?.bounds.height else { return nil }
        var top = fromTop
        while top < height - 4 {
            click(atTop: top, x: x, in: window)
            try await Task.sleep(for: .milliseconds(60))
            if hit() { return top }
            top += step
        }
        return nil
    }

    /// A click `top` points below the content's top edge, `x` from its left.
    static func click(atTop top: CGFloat, x: CGFloat, in window: NSWindow) {
        guard let height = window.contentView?.bounds.height else { return }
        click(NSPoint(x: x, y: height - top), in: window)
    }
}
