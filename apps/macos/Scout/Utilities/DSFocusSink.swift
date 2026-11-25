import AppKit
import SwiftUI

/// The view behind a focusable list that holds its keys (`dsFocusContainer`).
///
/// It is an AppKit view, not a SwiftUI `.focusable()` one, because SwiftUI
/// only lets code focus a `.focusable()` view it has already focused through
/// Tab once. On a page shown after the window opened, with the sidebar
/// holding the keys, setting the list's `@FocusState` from a click did
/// nothing until Tab had reached the list (hand check of #767). A first
/// responder AppKit view bound with `.focused(_:)` takes the keys every time,
/// and its `@FocusState` follows it.
///
/// It draws nothing, takes no clicks (hit testing passes through to the
/// list's items) and is hidden from VoiceOver: the items are what it reads.
/// Keys it gets go up the responder chain to the list's key handlers.
final class DSFocusSinkView: NSView {
    override var acceptsFirstResponder: Bool { true }
    override var focusRingType: NSFocusRingType {
        get { .none }
        set {}
    }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override func isAccessibilityElement() -> Bool { false }

    /// Nothing in `window` holds the keys: its first responder is the window
    /// or its content view, as when the app has just opened.
    static func keysAreFree(in window: NSWindow?) -> Bool {
        guard let window, let responder = window.firstResponder else { return true }
        return responder === window || responder === window.contentView
    }
}

/// `DSFocusSinkView` in SwiftUI. Only `dsFocusContainer` uses it.
struct DSFocusSink: NSViewRepresentable {
    func makeNSView(context: Context) -> DSFocusSinkView { DSFocusSinkView() }
    func updateNSView(_ view: DSFocusSinkView, context: Context) {}
}

extension View {
    /// Gives a focusable list the keys when it appears, if nothing holds
    /// them yet (the app has just opened on its page). Arriving from the
    /// sidebar leaves them there, so ↑ ↓ keep moving through the sidebar.
    /// Asked a turn after `onAppear`: until then the list's
    /// `DSFocusSinkView` isn't in its window, and SwiftUI drops a focus it
    /// can't give rather than keeping it for later.
    func dsFocusOnAppear(_ focus: FocusState<Bool>.Binding) -> some View {
        onAppear {
            DispatchQueue.main.async {
                if DSFocusSinkView.keysAreFree(in: NSApp.keyWindow) { focus.wrappedValue = true }
            }
        }
    }
}
