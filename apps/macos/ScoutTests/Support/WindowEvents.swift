import AppKit

/// Real key and mouse events into one in-process window, and the places to
/// click found through its accessibility tree. Nothing reaches other apps.
/// Tests that use it make their window key: run them in `KeyWindowGate`.
@MainActor
enum WindowEvents {
    static let tab = ("\t", UInt16(48))
    static let down = ("\u{F701}", UInt16(125))

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

    /// A left click at `point`, in the window's coordinates.
    static func click(_ point: NSPoint, in window: NSWindow) {
        for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
            guard let event = NSEvent.mouseEvent(
                with: type, location: point, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                windowNumber: window.windowNumber, context: nil, eventNumber: 0,
                clickCount: 1, pressure: type == .leftMouseDown ? 1 : 0
            ) else { continue }
            window.sendEvent(event)
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
