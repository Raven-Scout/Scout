import Foundation
import SwiftUI
import Testing
@testable import Scout

/// No keycap is typed by hand (UI audit #42: Done showed "⌘↵" for months with
/// nothing bound). Views build keycaps and tooltips through `ShortcutLabel`
/// from the `AppShortcut` they bind, so a label and its binding share one
/// value. This scans the app's sources for a keycap typed into a string
/// literal and names any. It can't prove the constant shown is also bound:
/// that's on the view (and on the system, for `undo` and `settings`).
@Suite("No hand-typed keycaps")
struct ShortcutLabelGuardTests {

    /// The formatter itself spells the modifier glyphs.
    static let allowlist: Set<String> = ["Utilities/AppShortcut.swift"]

    /// A string literal holding a modifier glyph: "⌘↵", "Close (⌘.)".
    static let pattern = #""[^"\n]*[⌃⌥⇧⌘][^"\n]*""#

    static var sourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout")
    }

    @Test("no Swift source types a keycap by hand")
    func noHandTypedKeycaps() throws {
        let root = Self.sourceRoot
        let enumerator = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        let regex = try NSRegularExpression(pattern: Self.pattern)
        var hits: [String] = []
        var scanned = 0
        for case let url as URL in enumerator where url.pathExtension == "swift" {
            let relative = String(url.path.dropFirst(root.path.count + 1))
            guard !Self.allowlist.contains(relative) else { continue }
            scanned += 1
            let text = try String(contentsOf: url, encoding: .utf8)
            for (index, line) in text.components(separatedBy: "\n").enumerated() {
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if trimmed.hasPrefix("//") { continue }
                if regex.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) != nil {
                    hits.append("\(relative):\(index + 1): \(trimmed)")
                }
            }
        }
        #expect(scanned > 100, "found only \(scanned) Swift files under \(root.path)")
        #expect(hits.isEmpty, """
            a keycap typed by hand can show a key nothing binds — bind an AppShortcut \
            and show it with ShortcutLabel.text/help:
            \(hits.joined(separator: "\n"))
            """)
    }

    @Test("the guard catches a hand-typed keycap")
    func patternMatches() throws {
        let regex = try NSRegularExpression(pattern: Self.pattern)
        func hits(_ s: String) -> Bool { regex.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) != nil }
        #expect(hits(#"EditorialActionButton("Done", shortcut: "⌘↵")"#))
        #expect(hits(#".help("Close (⌘.)")"#))
        #expect(hits(#"("⇧⌘F", "Expand")"#))
        #expect(!hits(#".help(ShortcutLabel.help("Close", AppShortcut.closeDetail))"#))
    }

    @Test("keycaps read the way macOS menus draw them")
    func labels() {
        #expect(ShortcutLabel.text(AppShortcut.commit) == "⌘↵")
        #expect(ShortcutLabel.text(AppShortcut.expandDetail) == "⇧⌘F")
        #expect(ShortcutLabel.text(AppShortcut.closeDetail) == "⌘.")
        #expect(ShortcutLabel.text(key: "k", modifiers: [.command, .option, .control, .shift]) == "⌃⌥⇧⌘K")
        #expect(ShortcutLabel.help("Close", AppShortcut.closeDetail) == "Close (⌘.)")
        #expect(ShortcutLabel.help("Mark done", nil) == "Mark done")
    }

    @Test("Done binds ⌘↵ only on the current card, and never over a comment editor")
    func doneShortcut() {
        let current = UUID(), other = UUID()
        var context = ActionItemsKeyboardContext(cursorID: current)
        #expect(context.doneShortcut(for: current) == AppShortcut.commit)
        #expect(context.doneShortcut(for: other) == nil)
        context.commitKeyFree = false
        #expect(context.doneShortcut(for: current) == nil)
        #expect(ActionItemsKeyboardContext().doneShortcut(for: current) == nil)
    }
}
