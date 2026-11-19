import Foundation
import Testing
@testable import Scout

/// Design-system v2: a message above or inside a page is a `DSBanner`
/// (`Utilities/DSBanner.swift`), or a `ProblemView`, which draws its banner
/// through it. Before v2 the Control Center stacked three banners in three
/// shapes (a 6pt rounded wash, a bare full-width bar, a stroked card), the
/// on-battery one set its sentence in the warning colour, and Schedules and
/// the KB editor each painted their own warning strip. This scans the app for
/// the hand-built forms: a status-coloured wash or bar painted behind a row,
/// and a `…Banner` view that doesn't render the component.
@Suite("Banners use the design-system banner")
struct BannerStyleGuardTests {

    /// Files allowed a status wash, and why.
    static let allowlist: [String: String] = [
        "Utilities/DSBanner.swift": "the component itself",
        "Utilities/DSChip.swift": "pills draw a tone wash",
    ]

    /// `…Banner` views that aren't page banners, and why.
    static let notBanners: [String: String] = [
        // A floating toast over the window's bottom edge, on material.
        "WriteProblemBanner": "toast",
    ]

    static let statusWash = try! NSRegularExpression(
        pattern: #"\.background\([^)]*DS\.Status\.|\.fill\(\s*DS\.Status\.(warn|err|ok)\.opacity\("#
            + #"|RoundedRectangle\([^)]*\)\s*\.strokeBorder\(\s*DS\.Status\.(warn|err|ok)\.opacity\("#)
    static let bannerStruct = try! NSRegularExpression(pattern: #"\bstruct\s+(\w+Banner)\b"#)

    static var sourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout")
    }

    static func sources() throws -> [(String, String)] {
        let root = sourceRoot
        let enumerator = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        var out: [(String, String)] = []
        for case let url as URL in enumerator where url.pathExtension == "swift" {
            let relative = String(url.path.dropFirst(root.path.count + 1))
            out.append((relative, try String(contentsOf: url, encoding: .utf8)))
        }
        return out
    }

    static func matches(_ regex: NSRegularExpression, _ line: String) -> Bool {
        regex.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) != nil
    }

    @Test("no status-coloured wash painted behind a row")
    func noHandBuiltWashes() throws {
        let files = try Self.sources()
        #expect(files.count > 100, "found only \(files.count) Swift files")
        var hits: [String] = []
        for (file, text) in files where Self.allowlist[file] == nil {
            for (i, line) in text.components(separatedBy: "\n").enumerated() {
                if line.trimmingCharacters(in: .whitespaces).hasPrefix("//") { continue }
                if Self.matches(Self.statusWash, line) {
                    hits.append("\(file):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))")
                }
            }
        }
        #expect(hits.isEmpty, """
            a banner built by hand — use DSBanner(tone:title:message:actions:onDismiss:), or \
            ProblemView for a Problem:
            \(hits.joined(separator: "\n"))
            """)
    }

    @Test("every …Banner view renders the design-system banner")
    func bannersRenderTheComponent() throws {
        var hits: [String] = []
        var found = 0
        for (file, text) in try Self.sources() where Self.allowlist[file] == nil {
            let range = NSRange(text.startIndex..., in: text)
            for match in Self.bannerStruct.matches(in: text, range: range) {
                guard let r = Range(match.range(at: 1), in: text) else { continue }
                let name = String(text[r])
                found += 1
                if Self.notBanners[name] != nil { continue }
                if !text.contains("DSBanner(") && !text.contains("ProblemView(") {
                    hits.append("\(file): \(name)")
                }
            }
        }
        #expect(found >= 3, "found only \(found) banner views; did the scan break?")
        #expect(hits.isEmpty, "these banners draw their own chrome — build them on DSBanner:\n\(hits.joined(separator: "\n"))")
    }

    @Test("the guard catches each form")
    func patternsMatch() {
        #expect(Self.matches(Self.statusWash, ".background(DS.Status.warn.opacity(0.15))"))
        #expect(Self.matches(Self.statusWash, ".background(critical ? DS.Status.err : DS.Status.warn)"))
        #expect(Self.matches(Self.statusWash, "RoundedRectangle(cornerRadius: 8).fill(DS.Status.err.opacity(0.08))"))
        #expect(Self.matches(Self.statusWash, ".overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(DS.Status.err.opacity(0.35), lineWidth: 1))"))
        #expect(!Self.matches(Self.statusWash, ".foregroundStyle(DS.Status.warn)"))
        #expect(!Self.matches(Self.statusWash, "Circle().fill(DS.Status.err)"))
        #expect(!Self.matches(Self.statusWash, ".overlay(Circle().strokeBorder(DS.Status.err.opacity(0.25), lineWidth: 3))"))
        #expect(!Self.matches(Self.statusWash, "case 1...2: return DS.Status.ok.opacity(0.22)"))
    }

    @Test("a banner's text is readable on every tone's wash")
    func bannerTextIsChecked() {
        let checked = Set(DS.ContrastPairs.all.map(\.description))
        for tone in DS.BannerTone.allCases {
            for text in DS.bannerText {
                #expect(checked.contains("\(text.name) on \(tone.tint.name) banner on Paper"),
                        "\(text.name) on the \(tone) banner isn't checked")
            }
        }
    }

    @Test("the allowlists name only things that exist")
    func allowlistIsCurrent() throws {
        for file in Self.allowlist.keys {
            #expect(FileManager.default.fileExists(atPath: Self.sourceRoot.appendingPathComponent(file).path),
                    "\(file) is allowlisted but gone")
        }
        let all = try Self.sources().map(\.1).joined()
        for name in Self.notBanners.keys {
            #expect(all.contains("struct \(name)"), "\(name) is allowlisted but gone")
        }
    }
}
