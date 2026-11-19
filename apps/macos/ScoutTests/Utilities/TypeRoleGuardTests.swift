import Foundation
import Testing

/// Design-system v2 (audit #104): views pick a type role, never a point size.
/// `DS.font(.caption)`, `DS.font(.body, .serif)` and `DS.font(.label, .mono)`
/// draw text; the sized `DS.sans/serif/mono(N)` are the scale's own building
/// blocks in `DesignSystem.swift`. Before v2 there were 714 size-based calls
/// at 26 sizes; this keeps new ones out.
///
/// To fix a hit, take the role at the nearest size (`DS.TextStyle`):
/// 8–9.5 micro · 10–10.5 caption2 · 11–11.5 caption · 12–12.5 label ·
/// 13–13.5 body · 14–16 headline · 17–20 title · 22–24 hero · 26–30 display.
/// An SF Symbol drawn as a picture takes `DS.symbol(N)`.
@Suite("Text uses the type roles, not point sizes")
struct TypeRoleGuardTests {

    /// The scale itself.
    static let allowlist: Set<String> = ["Utilities/DesignSystem.swift"]

    /// `DS.symbol` is for pictures: each use sits on an `Image(systemName:)`,
    /// except these, which size a glyph to the shape around it.
    static let symbolWithoutImage: Set<String> = ["Schedules/DayCircleStrip.swift"]

    static let sized = try! NSRegularExpression(pattern: #"\bDS\.(sans|serif|mono)\("#)
    static let symbol = try! NSRegularExpression(pattern: #"\bDS\.symbol\("#)

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
            guard !allowlist.contains(relative) else { continue }
            out.append((relative, try String(contentsOf: url, encoding: .utf8).components(separatedBy: "\n")))
        }
        return out
    }

    static func matches(_ regex: NSRegularExpression, _ line: String) -> Bool {
        regex.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) != nil
    }

    static func isComment(_ line: String) -> Bool {
        line.trimmingCharacters(in: .whitespaces).hasPrefix("//")
    }

    @Test("no size-based DS font calls outside the scale")
    func noSizedFonts() throws {
        let files = try Self.sources()
        #expect(files.count > 100, "found only \(files.count) Swift files")
        var hits: [String] = []
        for (file, lines) in files {
            for (i, line) in lines.enumerated() where !Self.isComment(line) && Self.matches(Self.sized, line) {
                hits.append("\(file):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))")
            }
        }
        #expect(hits.isEmpty, "pick a role — DS.font(.caption), DS.font(.body, .serif)…:\n\(hits.joined(separator: "\n"))")
    }

    @Test("DS.symbol sizes pictures, not text")
    func symbolIsForImages() throws {
        var hits: [String] = []
        for (file, lines) in try Self.sources() where !Self.symbolWithoutImage.contains(file) {
            for (i, line) in lines.enumerated() where !Self.isComment(line) && Self.matches(Self.symbol, line) {
                let around = lines[max(0, i - 2)...i].joined(separator: "\n")
                if !around.contains("Image(systemName") {
                    hits.append("\(file):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))")
                }
            }
        }
        #expect(hits.isEmpty, "DS.symbol on something that isn't an SF Symbol — text takes DS.font(role):\n\(hits.joined(separator: "\n"))")
    }

    @Test("the guard catches each sized form and passes the roles")
    func patternsMatch() {
        for sample in [".font(DS.sans(12))", ".font(DS.serif(28, weight: .medium))", "DS.mono(10.5)"] {
            #expect(Self.matches(Self.sized, sample), "\(sample)")
        }
        for clean in [".font(DS.font(.body))", ".font(DS.font(.caption, .mono))", "DS.symbol(36)"] {
            #expect(!Self.matches(Self.sized, clean), "\(clean)")
        }
    }

    @Test("the allowlists name only files that exist")
    func allowlistsAreCurrent() {
        for file in Self.allowlist.union(Self.symbolWithoutImage) {
            #expect(FileManager.default.fileExists(atPath: Self.sourceRoot.appendingPathComponent(file).path),
                    "\(file) is allowlisted but gone")
        }
    }
}
