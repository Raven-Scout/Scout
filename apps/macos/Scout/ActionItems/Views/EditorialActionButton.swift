import SwiftUI

/// Flat editorial action button: the handoff bundle's `.act` / `.act.primary`
/// language, with an optional keycap hint. Shared by the task action row and
/// the comment composer and editor so the card reads as one surface.
struct EditorialActionButton: View {
    enum Style { case primary, plain }

    let label: String
    let systemImage: String?
    let style: Style
    /// Keycap text shown inside the button, e.g. "⌘↵".
    let shortcut: String?
    /// Bound on the inner `Button` itself, so it works however the caller
    /// wraps this view.
    let keyboardShortcut: KeyboardShortcut?
    let action: () -> Void

    @Environment(\.isEnabled) private var isEnabled

    init(
        _ label: String,
        systemImage: String? = nil,
        style: Style = .plain,
        shortcut: String? = nil,
        keyboardShortcut: KeyboardShortcut? = nil,
        action: @escaping () -> Void
    ) {
        self.label = label
        self.systemImage = systemImage
        self.style = style
        self.shortcut = shortcut
        self.keyboardShortcut = keyboardShortcut
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                if let systemImage {
                    Image(systemName: systemImage)
                        .font(.system(size: 10))
                }
                Text(label)
                    .font(DS.sans(11.5, weight: .medium))
                if let shortcut {
                    Text(shortcut)
                        .font(DS.mono(10.5, weight: .medium))
                        .foregroundStyle(DS.Ink.p4)
                        .padding(.horizontal, 3)
                        .padding(.vertical, 1)
                        .overlay(RoundedRectangle(cornerRadius: 3).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                        .padding(.leading, 2)
                }
            }
            .foregroundStyle(style == .primary ? DS.Ink.p1 : DS.Ink.p3)
            .padding(.horizontal, 10)
            .frame(height: 24)
            .background {
                if style == .primary {
                    RoundedRectangle(cornerRadius: 5).fill(DS.Paper.raised)
                        .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(DS.Rule.hard, lineWidth: 0.5))
                }
            }
            .opacity(isEnabled ? 1 : 0.45)
        }
        .buttonStyle(.plainHit)
        .keyboardShortcut(keyboardShortcut)
        .help(label)
        // The system pointer style, not NSCursor push/pop: these buttons often
        // vanish on click (Cancel, Send), and a push without its pop leaves the
        // pointing hand stuck. A disabled button keeps the arrow.
        .pointerStyle(isEnabled ? .link : nil)
    }
}
