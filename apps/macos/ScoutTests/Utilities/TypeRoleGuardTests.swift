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

    /// A `Text` whose literal reads as words: three or more words of letters
    /// ("No tool activity recorded today."), or two words and then an
    /// interpolation ("Saved in \(path)").
    static let sentenceLiteral = try! NSRegularExpression(
        pattern: #"Text\("(?:(?:[A-Za-z][a-z'’]*[ ,.…]+){2,}[A-Za-z]|(?:[A-Za-z][a-z'’]*[ ,.…]+){2,}\\\()"#)
    static let monoVoice = try! NSRegularExpression(pattern: #"(DS\.font|dsFont)\([^)]*\.mono"#)

    /// The sentence-literal lines a container's body holds: from the line
    /// with `}` that closes it back to the line that opened it.
    static func blockLines(_ lines: [String], closingAt end: Int) -> ArraySlice<String> {
        var depth = 0
        for j in stride(from: end, through: max(0, end - 60), by: -1) {
            for ch in lines[j].reversed() {
                if ch == "}" { depth += 1 } else if ch == "{" { depth -= 1 }
            }
            if depth <= 0 { return lines[j...end] }
        }
        return lines[end...end]
    }

    /// Audit #108: monospace is for data and identifiers (paths, commands,
    /// times, counts, logs), never for sentences. A literal sentence set in
    /// mono, by its own font or its container's, reads as debug output.
    @Test("sentences aren't set in monospace")
    func noMonoSentences() throws {
        var hits: [String] = []
        for (file, lines) in try Self.sources() {
            for (i, line) in lines.enumerated() where !Self.isComment(line) && Self.matches(Self.sentenceLiteral, line) {
                // The Text's own font: on its line or the next two.
                let own = lines[i...min(lines.count - 1, i + 2)]
                if own.contains(where: { Self.matches(Self.monoVoice, $0) }) {
                    hits.append("\(file):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))")
                }
            }
            // A container's mono font: `}` then `.font(… .mono)` on the same
            // or the next line reaches every Text inside it.
            for (i, line) in lines.enumerated() where !Self.isComment(line) && Self.matches(Self.monoVoice, line) {
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                let close: Int? = trimmed.hasPrefix("}") ? i
                    : (i > 0 && lines[i - 1].trimmingCharacters(in: .whitespaces) == "}" ? i - 1 : nil)
                guard let close else { continue }
                for (k, inner) in Self.blockLines(lines, closingAt: close).enumerated()
                where !Self.isComment(inner) && Self.matches(Self.sentenceLiteral, inner) {
                    let lineNo = close - (Self.blockLines(lines, closingAt: close).count - 1) + k + 1
                    hits.append("\(file):\(lineNo): \(inner.trimmingCharacters(in: .whitespaces)) (container font at line \(i + 1))")
                }
            }
        }
        #expect(hits.isEmpty, "set sentences in the sans or serif voice; mono is for data:\n\(hits.joined(separator: "\n"))")
    }

    /// The serif voice needs its leading (`DS.extraLeading`), which only
    /// `View.dsFont` adds.
    @Test("serif text goes through dsFont")
    func serifUsesDsFont() throws {
        let bare = try NSRegularExpression(pattern: #"\.font\(DS\.font\([^)]*\.serif"#)
        var hits: [String] = []
        for (file, lines) in try Self.sources() {
            for (i, line) in lines.enumerated() where !Self.isComment(line) && Self.matches(bare, line) {
                hits.append("\(file):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))")
            }
        }
        #expect(hits.isEmpty, "use .dsFont(_:.serif) so serif text gets its leading:\n\(hits.joined(separator: "\n"))")
    }

    @Test("the guard catches each sized form and passes the roles")
    func patternsMatch() {
        for sample in [".font(DS.sans(12))", ".font(DS.serif(28, weight: .medium))", "DS.mono(10.5)"] {
            #expect(Self.matches(Self.sized, sample), "\(sample)")
        }
        for clean in [".font(DS.font(.body))", ".font(DS.font(.caption, .mono))", "DS.symbol(36)"] {
            #expect(!Self.matches(Self.sized, clean), "\(clean)")
        }
        // Sentences, literal or interpolated; not data.
        #expect(Self.matches(Self.sentenceLiteral, #"Text("No tool activity recorded today.")"#))
        #expect(Self.matches(Self.sentenceLiteral, #"Text("Saved in \(path)")"#))
        #expect(!Self.matches(Self.sentenceLiteral, #"Text("\(count)")"#))
        #expect(!Self.matches(Self.sentenceLiteral, #"Text("+\(n) more")"#))
        // A container's mono font reaches the sentence inside it.
        let block = ["HStack {", #"    Text("Done · two hours ago")"#, "}", ".font(DS.font(.label, .mono))"]
        #expect(Self.blockLines(block, closingAt: 2).count == 3)
    }

    @Test("the allowlists name only files that exist")
    func allowlistsAreCurrent() {
        for file in Self.allowlist.union(Self.symbolWithoutImage) {
            #expect(FileManager.default.fileExists(atPath: Self.sourceRoot.appendingPathComponent(file).path),
                    "\(file) is allowlisted but gone")
        }
    }
}
