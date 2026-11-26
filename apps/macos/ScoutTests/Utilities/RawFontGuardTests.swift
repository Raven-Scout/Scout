import Foundation
import Testing

/// Every font the app draws must go through the design system (`DS.sans`,
/// `DS.serif`, `DS.mono`, `DS.font`), because that is where Settings ▸
/// General ▸ Text size is applied (C7). A raw `.system(size:)` or a
/// text-style font (`.font(.caption)`, `.body.weight(…)`) silently stays at
/// its fixed size. This scans the app's sources and names any that slipped in.
///
/// To fix a hit, use the `DS.font` role at the nearest size (macOS: body 13,
/// callout 12, subheadline 11, caption/footnote 10, headline 13 bold, title3
/// 15, title2 17, title 22, largeTitle 26); `TypeRoleGuardTests` lists which
/// role covers which size.
@Suite("No raw fonts outside the design system")
struct RawFontGuardTests {

    /// Files allowed to build fonts directly: the design system itself, and
    /// `KindMarker`, which sizes an SF Symbol to its own marker diameter.
    static let allowlist: Set<String> = ["Utilities/DesignSystem.swift", "Utilities/KindMarker.swift"]

    static let styles = "largeTitle|title|title2|title3|headline|subheadline|body|callout|footnote|caption|caption2"
    static let patterns: [String] = [
        #"\.system\(size:"#,                                   // .system(size: 12) / Font.system(size:)
        #"(?<!DS)\.font\(\.(\#(styles))\b"#,                     // .font(.caption), .font(.body.weight(.bold)); not DS.font(.body)
        #"\.system\(\.(\#(styles))\b"#,                        // .system(.callout, design: .monospaced)
        #"\bFont\.(\#(styles))\b"#,                            // Font.caption
    ]

    static var sourceRoot: URL {
        // …/apps/macos/ScoutTests/Utilities/RawFontGuardTests.swift → …/apps/macos/Scout
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout")
    }

    @Test("app sources build fonts only through DS")
    func noRawFonts() throws {
        let root = Self.sourceRoot
        let enumerator = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        let regexes = try Self.patterns.map { try NSRegularExpression(pattern: $0) }
        var hits: [String] = []
        var scanned = 0
        for case let url as URL in enumerator where url.pathExtension == "swift" {
            let relative = String(url.path.dropFirst(root.path.count + 1))
            guard !Self.allowlist.contains(relative) else { continue }
            scanned += 1
            let text = try String(contentsOf: url, encoding: .utf8)
            for (index, line) in text.components(separatedBy: "\n").enumerated() {
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if trimmed.hasPrefix("//") || trimmed.hasPrefix("///") { continue }
                let range = NSRange(line.startIndex..., in: line)
                if regexes.contains(where: { $0.firstMatch(in: line, range: range) != nil }) {
                    hits.append("\(relative):\(index + 1): \(trimmed)")
                }
            }
        }
        #expect(scanned > 100, "found only \(scanned) Swift files under \(root.path)")
        #expect(hits.isEmpty, "raw fonts bypass Text size — use DS.sans/serif/mono:\n\(hits.joined(separator: "\n"))")
    }

    @Test("the guard catches each raw form")
    func patternsMatch() throws {
        let regexes = try Self.patterns.map { try NSRegularExpression(pattern: $0) }
        let samples = [".font(.system(size: 12))", "Font.system(size: 13, weight: .bold)", ".font(.caption)",
                       ".font(.body.weight(.semibold))", ".font(.system(.callout, design: .monospaced))",
                       "let f = Font.headline"]
        for sample in samples {
            let range = NSRange(sample.startIndex..., in: sample)
            #expect(regexes.contains { $0.firstMatch(in: sample, range: range) != nil }, "\(sample)")
        }
        for clean in [".font(DS.sans(12))", ".font(DS.font(.body))", "NSFont.systemFont(ofSize: 13)"] {
            let range = NSRange(clean.startIndex..., in: clean)
            #expect(!regexes.contains { $0.firstMatch(in: clean, range: range) != nil }, "\(clean)")
        }
    }
}
