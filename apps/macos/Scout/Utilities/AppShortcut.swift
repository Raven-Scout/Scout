import SwiftUI

/// The app's own keyboard shortcuts, in one place, so the key a view binds and
/// the keycap or tooltip it shows come from the same value. A label typed by
/// hand drifted from its binding: Done showed "⌘↵" for months with nothing
/// bound (UI audit #42). `ShortcutLabelGuardTests` fails on a keycap typed into
/// a string literal; build it with `ShortcutLabel.text(_:)` instead.
nonisolated enum AppShortcut {
    /// Action Items: mark the keyboard-current card done; in a comment editor,
    /// send or save the comment. Never both at once (see `TaskCardView`).
    static let commit = KeyboardShortcut(.return, modifiers: .command)
    /// Find on the current page (Action Items search, find in a KB note).
    static let find = KeyboardShortcut("f", modifiers: .command)
    static let findNext = KeyboardShortcut("g", modifiers: .command)
    static let findPrevious = KeyboardShortcut("g", modifiers: [.command, .shift])
    /// Knowledge Base: search every note, from anywhere on the page.
    static let searchNotes = KeyboardShortcut("f", modifiers: [.command, .shift])
    /// Run details (Control Center, Wishlist, Research).
    static let closeDetail = KeyboardShortcut(".", modifiers: .command)
    static let expandDetail = KeyboardShortcut("f", modifiers: [.command, .shift])
    /// Action Items select mode: copy the selected items.
    static let copySelected = KeyboardShortcut("c", modifiers: [.command, .shift])

    /// Bound by the system, not by a view of ours: Edit ▸ Undo and the app
    /// menu's Settings…. The only keycaps a label may show without binding.
    static let undo = KeyboardShortcut("z", modifiers: .command)
    static let settings = KeyboardShortcut(",", modifiers: .command)
}

/// Keycap text for a shortcut, modifiers in the order macOS menus draw them
/// (⌃⌥⇧⌘), then the key: "⌘↵", "⇧⌘F".
nonisolated enum ShortcutLabel {
    static func text(_ shortcut: KeyboardShortcut) -> String {
        text(key: shortcut.key, modifiers: shortcut.modifiers)
    }

    static func text(key: KeyEquivalent, modifiers: EventModifiers) -> String {
        var out = ""
        if modifiers.contains(.control) { out += "⌃" }
        if modifiers.contains(.option) { out += "⌥" }
        if modifiers.contains(.shift) { out += "⇧" }
        if modifiers.contains(.command) { out += "⌘" }
        return out + keyText(key)
    }

    /// "Mark done (⌘↵)", or the bare title when there is no shortcut.
    static func help(_ title: String, _ shortcut: KeyboardShortcut?) -> String {
        guard let shortcut else { return title }
        return "\(title) (\(text(shortcut)))"
    }

    private static func keyText(_ key: KeyEquivalent) -> String {
        switch key {
        case .return:     return "↵"
        case .escape:     return "⎋"
        case .delete:     return "⌫"
        case .tab:        return "⇥"
        case .space:      return "Space"
        case .upArrow:    return "↑"
        case .downArrow:  return "↓"
        case .leftArrow:  return "←"
        case .rightArrow: return "→"
        case .home:       return "↖"
        case .end:        return "↘"
        default:          return String(key.character).uppercased()
        }
    }
}
