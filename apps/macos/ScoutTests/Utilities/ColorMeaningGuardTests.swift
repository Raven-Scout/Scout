import Foundation
import Testing

/// Design-system v2 (audit #107): a colour means one thing. The urgent red
/// means Urgent and nothing else: "Mine" and "needs you" take
/// `DS.Attention.you`, a note type takes its own hue, a warning takes
/// `DS.Status.warn`. And no view paints with a system named colour
/// (`Color.yellow`, `.foregroundStyle(.red)`), which `ContrastPairsTests`
/// can't check and which ignores the palette's dark and Increase Contrast
/// values.
@Suite("Colours carry one meaning")
struct ColorMeaningGuardTests {

    /// The only files that may draw `DS.Priority.urgent`: the places that
    /// show an item's urgency.
    static let urgentRedFiles: Set<String> = [
        "Utilities/DesignSystem.swift",               // priorityColor(.urgent)
        "ActionItems/Views/SectionView.swift",         // the Urgent section's priority level
        "PerFileItems/Views/ItemPriorityPill.swift",   // a wishlist item's Urgent pill
        "Shell/MenuBarExtraContent.swift",             // the menu-bar panel's urgent count
    ]

    static let urgentRed = try! NSRegularExpression(pattern: #"DS\.Priority\.urgent\b"#)
    static let systemNamed = try! NSRegularExpression(
        pattern: #"\bColor\.(red|orange|yellow|green|mint|teal|cyan|blue|indigo|purple|pink|brown)\b"#
            + #"|\.(foregroundColor|foregroundStyle|fill|tint|background)\(\.(red|orange|yellow|green|mint|teal|cyan|blue|indigo|purple|pink|brown)\b"#)

    static var sourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout")
    }

    static func sources() throws -> [(String, [String])] {
        let root = sourceRoot
        let enumerator = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        var out: [(String, [String])] = []
        for case let url as URL in enumerator where url.pathExtension == "swift" {
            let relative = String(url.path.dropFirst(root.path.count + 1))
            out.append((relative, try String(contentsOf: url, encoding: .utf8).components(separatedBy: "\n")))
        }
        return out
    }

    static func hits(_ regex: NSRegularExpression, skipping skip: Set<String> = []) throws -> [String] {
        var hits: [String] = []
        for (file, lines) in try sources() where !skip.contains(file) {
            for (i, line) in lines.enumerated() {
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if trimmed.hasPrefix("//") { continue }
                if regex.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) != nil {
                    hits.append("\(file):\(i + 1): \(trimmed)")
                }
            }
        }
        return hits
    }

    @Test("the urgent red is drawn only where something's urgency is shown")
    func urgentRedMeansUrgent() throws {
        let hits = try Self.hits(Self.urgentRed, skipping: Self.urgentRedFiles)
        #expect(hits.isEmpty, """
            DS.Priority.urgent means Urgent only. For "yours"/"needs you" use DS.Attention.you; \
            for a warning DS.Status.warn or .err; for a category, its own swatch:
            \(hits.joined(separator: "\n"))
            """)
    }

    @Test("no system named colours")
    func noSystemNamedColours() throws {
        let hits = try Self.hits(Self.systemNamed)
        #expect(hits.isEmpty, "use a DS token (DS.Status, DS.Priority, DS.Attention…):\n\(hits.joined(separator: "\n"))")
    }

    @Test("the guard catches each form")
    func patternsMatch() {
        func matches(_ s: String) -> Bool {
            Self.systemNamed.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) != nil
        }
        #expect(matches(".background(Color.yellow.opacity(0.15))"))
        #expect(matches(".foregroundColor(.yellow)"))
        #expect(matches(".foregroundStyle(.red)"))
        #expect(!matches(".foregroundStyle(DS.Status.warn)"))
        #expect(!matches(".foregroundStyle(.secondary)"))
        #expect(!matches("Color.clear"))
    }

    @Test("the urgent-red allowlist names only files that exist")
    func allowlistIsCurrent() {
        for file in Self.urgentRedFiles {
            #expect(FileManager.default.fileExists(atPath: Self.sourceRoot.appendingPathComponent(file).path),
                    "\(file) is allowlisted but gone")
        }
    }
}
