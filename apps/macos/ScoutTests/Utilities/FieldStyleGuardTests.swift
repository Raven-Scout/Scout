import Foundation
import Testing
@testable import Scout

/// Design-system v2: a text input is `TextField(…).dsField()`, which draws
/// the recessed well and its focus, disabled and error states. Before v2,
/// Settings and onboarding each hand-built the same well, and neither drew
/// focus. This keeps new hand-built wells out: a `.plain` field whose own
/// modifier chain paints a `Paper.sunk` background.
///
/// Search fields (an icon, the field and a clear button inside one well) are
/// a different component and aren't flagged: their well is on the row.
@Suite("Text inputs use the design-system field")
struct FieldStyleGuardTests {

    static var sourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout")
    }

    @Test("no text field hand-builds the field well")
    func noHandBuiltWells() throws {
        let root = Self.sourceRoot
        let enumerator = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        var hits: [String] = []
        var scanned = 0
        for case let url as URL in enumerator where url.pathExtension == "swift" {
            let relative = String(url.path.dropFirst(root.path.count + 1))
            guard relative != "Utilities/DSField.swift" else { continue }
            scanned += 1
            let lines = try String(contentsOf: url, encoding: .utf8).components(separatedBy: "\n")
            for (i, line) in lines.enumerated() where line.contains(".textFieldStyle(.plain)") {
                // The field's own chain: the following lines up to the first
                // closing brace.
                var chain = [line]
                for next in lines.dropFirst(i + 1).prefix(6) {
                    if next.trimmingCharacters(in: .whitespaces).hasPrefix("}") { break }
                    chain.append(next)
                }
                if chain.contains(where: { $0.contains("Paper.sunk") }) {
                    hits.append("\(relative):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))")
                }
            }
        }
        #expect(scanned > 100)
        #expect(hits.isEmpty, "use TextField(…).dsField() instead of building the well:\n\(hits.joined(separator: "\n"))")
    }

    @Test("a field's text is readable on its well in every state")
    func fieldTextIsChecked() {
        let paperText = Set(DS.ContrastPairs.paperText.map(\.name))
        let surfaces = Set(DS.ContrastPairs.paperSurfaces.map(\.swatch.name))
        for enabled in [true, false] {
            for error in [true, false] {
                let c = DS.fieldColors(enabled: enabled, error: error)
                #expect(paperText.contains(c.text.name), "\(c.text.name) isn't a checked text colour")
                #expect(surfaces.contains(c.fill.name), "\(c.fill.name) isn't a checked surface")
            }
        }
        #expect(DS.fieldColors(enabled: true, error: true).stroke.name == DS.Palette.statusErr.name)
        #expect(DS.fieldColors(enabled: false, error: false).text.name == DS.Palette.inkP4.name,
                "a disabled field looks like a disabled button")
    }
}
