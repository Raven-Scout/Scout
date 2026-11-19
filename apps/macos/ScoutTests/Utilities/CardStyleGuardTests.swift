import Foundation
import Testing
@testable import Scout

/// Design-system v2: a card is `.editorialCard(...)`, which draws the raised
/// paper, its hairline, the coloured edge and the selected, marked, hover,
/// pressed and focus states; a clickable card wraps it in
/// `.buttonStyle(.dsCard(cornerRadius:))`. Before v2, ten views built the
/// chrome inline and drew "selected" five different ways (an accent wash, an
/// ink outline, a 2pt accent ring, a type-coloured ring, a leading ink bar),
/// and only two drew hover.
///
/// This flags raised paper drawn in a rounded rectangle of card size (radius
/// 8 or more) outside the design system. Small radii are buttons, segments
/// and tags. A surface that isn't a card (a floating panel, the timeline's
/// axis track) says so on the line with `// ds-guard: not a card — <why>`.
@Suite("Cards use the design-system card")
struct CardStyleGuardTests {

    static let notACardMarker = "ds-guard: not a card"
    static let raised = "DS.Paper.raised"
    static let cardRadius = try! NSRegularExpression(pattern: #"RoundedRectangle\(cornerRadius:\s*(\d+)"#)

    /// The design system's own files.
    static let allowlist: Set<String> = ["Utilities/DesignSystem.swift"]

    static var sourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout")
    }

    /// Hand-built card chrome in `lines`: a line drawing raised paper with a
    /// card-sized rounded rectangle within three lines of it.
    static func handBuiltCards(_ lines: [String]) -> [Int] {
        var out: [Int] = []
        for (i, line) in lines.enumerated() {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard line.contains(raised), !trimmed.hasPrefix("//") else { continue }
            let window = lines[max(0, i - 3)...min(lines.count - 1, i + 3)]
            if window.contains(where: { $0.contains(notACardMarker) }) { continue }
            let cardSized = window.contains { w in
                cardRadius.matches(in: w, range: NSRange(w.startIndex..., in: w)).contains { m in
                    guard let r = Range(m.range(at: 1), in: w), let radius = Int(w[r]) else { return false }
                    return radius >= 8
                }
            }
            if cardSized { out.append(i) }
        }
        return out
    }

    @Test("no hand-built card chrome")
    func noHandBuiltCards() throws {
        let root = Self.sourceRoot
        let enumerator = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        var hits: [String] = []
        var scanned = 0
        for case let url as URL in enumerator where url.pathExtension == "swift" {
            let relative = String(url.path.dropFirst(root.path.count + 1))
            guard !Self.allowlist.contains(relative) else { continue }
            scanned += 1
            let lines = try String(contentsOf: url, encoding: .utf8).components(separatedBy: "\n")
            for i in Self.handBuiltCards(lines) {
                hits.append("\(relative):\(i + 1): \(lines[i].trimmingCharacters(in: .whitespaces))")
            }
        }
        #expect(scanned > 100, "found only \(scanned) Swift files")
        #expect(hits.isEmpty, """
            card chrome built by hand — use .editorialCard(padding:cornerRadius:selected:accentBar:), and \
            .buttonStyle(.dsCard(cornerRadius:)) when the whole card is clickable; mark a surface that isn't a \
            card "// \(Self.notACardMarker) — <why>":
            \(hits.joined(separator: "\n"))
            """)
    }

    @Test("the guard catches each form")
    func patternsMatch() {
        #expect(Self.handBuiltCards([
            ".background(",
            "    RoundedRectangle(cornerRadius: 8)",
            "        .fill(DS.Paper.raised)",
            "        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(DS.Rule.soft, lineWidth: 0.5))",
            ")",
        ]) == [2])
        #expect(Self.handBuiltCards([
            ".background(DS.Paper.raised, in: RoundedRectangle(cornerRadius: 10, style: .continuous))",
        ]) == [0])
        #expect(Self.handBuiltCards([
            "RoundedRectangle(cornerRadius: 8)",
            "    .fill(hovering ? DS.Paper.raised : DS.Paper.raised.opacity(0.5))",
        ]) == [1])
        // A segment, a tag, a button: small radii aren't cards.
        #expect(Self.handBuiltCards([
            "RoundedRectangle(cornerRadius: 5).fill(DS.Paper.raised)",
        ]).isEmpty)
        // A surface that says it isn't a card.
        #expect(Self.handBuiltCards([
            "RoundedRectangle(cornerRadius: 12)  // ds-guard: not a card — the side panel",
            "    .fill(DS.Paper.raised)",
        ]).isEmpty)
    }
}
