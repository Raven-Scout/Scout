import Testing
import AppKit
@testable import Scout

/// The menu-bar update dot is baked into a single template `NSImage`, because
/// a `MenuBarExtra` label keeps only its Image/Text and drops SwiftUI
/// overlays. Whether it reads well at menu-bar size is a manual visual check.
@Suite("MenuBarIcon")
@MainActor
struct MenuBarIconTests {
    static let allStatuses: [AppState.MenuBarStatus] = [.idle, .running, .lastFailed, .budgetSkipped]

    @Test func badgedImageIsATemplateTheSizeOfTheBaseSymbol() throws {
        for status in Self.allStatuses {
            let name = MenuBarIcon.symbolName(for: status)
            let base = try #require(MenuBarIcon.baseSymbol(named: name))
            let badged = try #require(MenuBarIcon.badgedImage(symbolName: name))
            #expect(badged.isTemplate)
            #expect(badged.size == base.size)
        }
    }

    @Test func badgedImageDrawsTheDotInsideTheTopTrailingCorner() throws {
        let name = MenuBarIcon.symbolName(for: .idle)
        let badged = try #require(MenuBarIcon.badgedImage(symbolName: name))
        let dot = MenuBarIcon.dotRect(in: CGRect(origin: .zero, size: badged.size))
        #expect(CGRect(origin: .zero, size: badged.size).contains(dot))
        #expect(dot.maxX == badged.size.width)
        #expect(dot.maxY == badged.size.height)   // unflipped: maxY is the top edge

        // Rendering runs the drawing handler: the dot's centre is opaque.
        var proposed = CGRect(origin: .zero, size: badged.size)
        let cg = try #require(badged.cgImage(forProposedRect: &proposed, context: nil, hints: nil))
        let rep = NSBitmapImageRep(cgImage: cg)
        let scale = CGFloat(rep.pixelsWide) / badged.size.width
        // NSBitmapImageRep pixel rows run top-down, so the top edge is y = 0.
        let x = Int((dot.midX * scale).rounded(.down))
        let y = Int(((badged.size.height - dot.midY) * scale).rounded(.down))
        let alpha = try #require(rep.colorAt(x: x, y: y)).alphaComponent
        #expect(alpha > 0.9)
    }

    @Test func unknownSymbolYieldsNoBadgedImage() {
        #expect(MenuBarIcon.badgedImage(symbolName: "no.such.symbol.scout") == nil)
    }
}
