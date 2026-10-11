import SwiftUI
import AppKit

struct MenuBarIcon: View {
    let status: AppState.MenuBarStatus
    /// Draws a small dot on the icon when either update track is behind, so
    /// an update reads as a notification even with the window closed.
    var updateAvailable: Bool = false

    var body: some View {
        // A `MenuBarExtra` label keeps only its Image/Text — SwiftUI overlays
        // and offsets are dropped — so the badged variant is one AppKit-drawn
        // template image with the dot baked in.
        if updateAvailable, let badged = Self.badgedImage(symbolName: Self.symbolName(for: status)) {
            Image(nsImage: badged)
                .accessibilityLabel("Scout — update available")
                .help("Scout — update available")
        } else {
            Image(systemName: Self.symbolName(for: status))
        }
    }

    static func symbolName(for status: AppState.MenuBarStatus) -> String {
        switch status {
        case .idle:          "bolt"
        case .running:       "circle.dotted"
        case .lastFailed:    "exclamationmark.triangle"
        case .budgetSkipped: "pause.circle"
        }
    }

    /// The menu bar draws its symbols at the system font size; the badged
    /// image uses the same point size so it doesn't jump when the dot appears.
    static let symbolConfiguration = NSImage.SymbolConfiguration(pointSize: NSFont.systemFontSize, weight: .regular)

    static func baseSymbol(named name: String) -> NSImage? {
        NSImage(systemSymbolName: name, accessibilityDescription: nil)?
            .withSymbolConfiguration(symbolConfiguration)
    }

    /// The dot's rect, in the top-trailing corner and wholly inside `bounds`
    /// (unflipped coordinates: maxY is the top edge).
    static func dotRect(in bounds: CGRect) -> CGRect {
        let diameter = (min(bounds.width, bounds.height) * 0.42).rounded()
        return CGRect(x: bounds.maxX - diameter, y: bounds.maxY - diameter, width: diameter, height: diameter)
    }

    /// The status symbol with an update dot composited inside its bounds. A
    /// ring around the dot is cut out of the symbol so the dot reads as
    /// separate at menu-bar size. `isTemplate` so the menu bar tints it like
    /// any other status item (light, dark, highlighted).
    static func badgedImage(symbolName name: String) -> NSImage? {
        guard let base = baseSymbol(named: name) else { return nil }
        let size = base.size
        let image = NSImage(size: size, flipped: false) { rect in
            base.draw(in: rect)
            let dot = dotRect(in: rect)
            guard let context = NSGraphicsContext.current else { return true }
            let ring: CGFloat = 1.5
            context.saveGraphicsState()
            context.compositingOperation = .clear
            NSBezierPath(ovalIn: dot.insetBy(dx: -ring, dy: -ring)).fill()
            context.restoreGraphicsState()
            NSColor.black.setFill()
            NSBezierPath(ovalIn: dot).fill()
            return true
        }
        image.isTemplate = true
        image.accessibilityDescription = "Scout — update available"
        return image
    }
}
