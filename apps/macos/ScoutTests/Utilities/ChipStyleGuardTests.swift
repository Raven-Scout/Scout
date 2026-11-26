import Foundation
import Testing
@testable import Scout

/// Design-system v2: a status pill is `DSPill`, a filter chip is
/// `.buttonStyle(.dsChip(selected:))`, a small boxed tag is `.dsTag()`
/// (`Utilities/DSChip.swift`). Before v2 there were ten hand-built recipes
/// with three different "selected" looks, and several pills coloured their
/// text with hues no contrast test checked. This scans the app's sources for
/// the hand-built forms:
///
/// - a filled `Capsule()` (`Capsule().fill(…)`, `.background(Capsule()…)`,
///   `background(…, in: Capsule())`),
/// - the old `EditorialChipBackground` box,
/// - a small rounded rectangle (radius 5 or less) filled with a tint wash.
@Suite("Chips and pills use the design-system components")
struct ChipStyleGuardTests {

    /// Files allowed a filled capsule or a small wash, and why.
    static let allowlist: [String: String] = [
        "Utilities/DSChip.swift": "the component itself",
        // A switch's track and knob, not a chip.
        "Shell/SettingsComponents.swift": "toggle switch",
        // Step dots in the onboarding header.
        "Onboarding/OnboardingView.swift": "progress dots",
        // The floating undo toast: a material capsule, not a pill.
        "Shell/WriteFeedbackOverlay.swift": "toast",
    ]

    static let filledCapsule = try! NSRegularExpression(
        pattern: #"Capsule\(\)\s*\.fill\(|\.background\(\s*Capsule\(\)|in:\s*Capsule\(\)"#)
    static let chipBox = try! NSRegularExpression(pattern: #"\bEditorialChipBackground\b"#)
    static let smallWash = try! NSRegularExpression(
        pattern: #"RoundedRectangle\(cornerRadius:\s*[1-5](\.\d+)?\b[^\n]*\.opacity\(0\.[01]\d*\)"#
            + #"|\.opacity\(0\.[01]\d*\)[^\n]*in:\s*RoundedRectangle\(cornerRadius:\s*[1-5](\.\d+)?\b"#)

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
            guard allowlist[relative] == nil else { continue }
            out.append((relative, try String(contentsOf: url, encoding: .utf8).components(separatedBy: "\n")))
        }
        return out
    }

    static func matches(_ regex: NSRegularExpression, _ line: String) -> Bool {
        regex.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) != nil
    }

    static func hits(_ regex: NSRegularExpression) throws -> [String] {
        var hits: [String] = []
        let files = try sources()
        #expect(files.count > 100, "found only \(files.count) Swift files")
        for (file, lines) in files {
            for (i, line) in lines.enumerated() {
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if trimmed.hasPrefix("//") || trimmed.hasPrefix("///") { continue }
                if matches(regex, line) { hits.append("\(file):\(i + 1): \(trimmed)") }
            }
        }
        return hits
    }

    @Test("no hand-built capsule pills or chips")
    func noFilledCapsules() throws {
        let hits = try Self.hits(Self.filledCapsule)
        #expect(hits.isEmpty, """
            a capsule built by hand — use DSPill(_:tone:) for a status, .buttonStyle(.dsChip(selected:)) \
            for a filter chip:
            \(hits.joined(separator: "\n"))
            """)
    }

    @Test("no hand-built chip boxes")
    func noChipBoxes() throws {
        let hits = try Self.hits(Self.chipBox)
        #expect(hits.isEmpty, "use .dsTag() instead of EditorialChipBackground:\n\(hits.joined(separator: "\n"))")
    }

    @Test("no small tint-wash badges")
    func noSmallWashes() throws {
        let hits = try Self.hits(Self.smallWash)
        #expect(hits.isEmpty, "a tinted badge built by hand — use DSPill(_:tone:):\n\(hits.joined(separator: "\n"))")
    }

    @Test("the guard catches each form")
    func patternsMatch() {
        #expect(Self.matches(Self.filledCapsule, ".background(Capsule().fill(tint.opacity(0.14)))"))
        #expect(Self.matches(Self.filledCapsule, "Capsule().fill(isSelected ? DS.Ink.p1 : DS.Paper.raised)"))
        #expect(Self.matches(Self.filledCapsule, ".background(background, in: Capsule())"))
        #expect(!Self.matches(Self.filledCapsule, ".contentShape(Capsule())"))
        #expect(!Self.matches(Self.filledCapsule, ".clipShape(Capsule())"))
        #expect(Self.matches(Self.chipBox, ".background(EditorialChipBackground())"))
        #expect(Self.matches(Self.smallWash, "RoundedRectangle(cornerRadius: 4).fill(color.opacity(0.12))"))
        #expect(Self.matches(Self.smallWash, ".background(Color.secondary.opacity(0.15), in: RoundedRectangle(cornerRadius: 4))"))
        #expect(!Self.matches(Self.smallWash, "RoundedRectangle(cornerRadius: 8).fill(tint.opacity(0.08))"))
        #expect(!Self.matches(Self.smallWash, "RoundedRectangle(cornerRadius: 5).fill(DS.Paper.sunk)"))
    }

    @Test("every pill's text is checked on its wash, and every chip state is a checked pair")
    func pillAndChipColoursAreChecked() {
        let checked = Set(DS.ContrastPairs.all.map { "\($0.foreground.name) on \($0.background.name)" })
        for tone in DS.PillTone.all {
            let c = DS.pillColors(tone)
            let surfaces = DS.ContrastPairs.pillSurfaces(c)
            #expect(!surfaces.isEmpty)
            for s in surfaces {
                #expect(checked.contains("\(c.text.name) on \(s.name)"), "\(tone): \(c.text.name) on \(s.name) unchecked")
            }
        }
        let paperText = Set(DS.ContrastPairs.paperText.map(\.name))
        let surfaces = Set(DS.ContrastPairs.paperSurfaces.map(\.swatch.name))
        for enabled in [true, false] {
            for hovering in [true, false] {
                let c = DS.chipColors(selected: false, enabled: enabled, hovering: hovering)
                #expect(paperText.contains(c.label.name) && surfaces.contains(c.fill.name), "\(c.label.name) on \(c.fill.name)")
                let count = DS.chipCountColor(selected: false, enabled: enabled)
                #expect(paperText.contains(count.name))
            }
        }
        let selected = DS.chipColors(selected: true, enabled: true, hovering: false)
        #expect(checked.contains("\(selected.label.name) on Ink.p1"))
        #expect(DS.chipCountColor(selected: true, enabled: true).name == selected.label.name)
        for link in [true, false] {
            for enabled in [true, false] {
                #expect(paperText.contains(DS.tagTextColor(link: link, enabled: enabled).name))
            }
        }
    }

    @Test("the allowlist names only files that exist")
    func allowlistIsCurrent() {
        for file in Self.allowlist.keys {
            #expect(FileManager.default.fileExists(atPath: Self.sourceRoot.appendingPathComponent(file).path),
                    "\(file) is allowlisted but gone — drop it from the allowlist")
        }
    }
}
