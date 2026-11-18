import Foundation
import Testing

/// Design-system v2 (#105): actions use the DS button styles (`.dsPrimary`,
/// `.dsSecondary`, `.dsLink`, `.dsDestructive`), so every button draws the
/// same disabled, pressed and focus states. This scans the app's sources for
/// the two things that drift from that: the system bordered styles, and an
/// accent button hand-built from `.plainHit`/`.plain` plus accent colours.
///
/// To fix a hit, pick the DS role (see `Utilities/DSButtonStyle.swift`) and
/// drop the caller's `.font`, `.foregroundStyle`, padding and background:
/// the style draws them.
@Suite("Buttons use the design-system styles")
struct ButtonStyleGuardTests {

    /// Files allowed a system or hand-built style, and why.
    static let allowlist: [String: String] = [
        // A toolbar control: the window toolbar draws its own bezel.
        "ActionItems/Views/DatePickerToolbarItem.swift": "toolbar",
        // The status-bar health cell: the whole cell is the hit area, and its
        // accent text is a "fix" hint inside it, not a button label.
        "Shell/StatusBarView.swift": "hit area",
    ]

    /// Folders skipped whole. The Knowledge Base is being rebuilt as a web
    /// view (Raven-Scout/Scout#677); its buttons move to DS styles there, or
    /// right after it lands.
    static let allowedFolders: [String: String] = ["KnowledgeBase/": "#677"]

    static let systemStyle = try! NSRegularExpression(
        pattern: #"\.buttonStyle\(\.(bordered|borderedProminent|link|borderless)\)"#)
    static let plainStyle = try! NSRegularExpression(pattern: #"\.buttonStyle\(\.(plainHit|plain)\)"#)
    /// Accent colours on a plain button make it a hand-built primary or link.
    static let accentColour = try! NSRegularExpression(
        pattern: #"foregroundStyle\(DS\.Accent\.(ink|onFill)\)|\.fill\(DS\.Accent\.fill\)|background\(DS\.Accent\.fill"#)

    static var sourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout")
    }

    /// Every app source file, relative path → lines, minus the allowlist.
    static func sources() throws -> [(String, [String])] {
        let root = sourceRoot
        let enumerator = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        var out: [(String, [String])] = []
        for case let url as URL in enumerator where url.pathExtension == "swift" {
            let relative = String(url.path.dropFirst(root.path.count + 1))
            guard allowlist[relative] == nil,
                  !allowedFolders.keys.contains(where: { relative.hasPrefix($0) }) else { continue }
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

    @Test("no system bordered or link button styles")
    func noSystemStyles() throws {
        let files = try Self.sources()
        #expect(files.count > 100, "found only \(files.count) Swift files")
        // `.borderless` stays allowed for icon-only buttons (the menu-bar
        // footer); only the text-button styles are flagged.
        var hits: [String] = []
        for (file, lines) in files {
            for (i, line) in lines.enumerated() where !Self.isComment(line) && Self.matches(Self.systemStyle, line) {
                if line.contains(".borderless") { continue }
                hits.append("\(file):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))")
            }
        }
        #expect(hits.isEmpty, "use .dsPrimary/.dsSecondary/.dsLink instead:\n\(hits.joined(separator: "\n"))")
    }

    @Test("no accent buttons hand-built from .plainHit or .plain")
    func noHandBuiltAccentButtons() throws {
        var hits: [String] = []
        for (file, lines) in try Self.sources() {
            for (i, line) in lines.enumerated() where !Self.isComment(line) && Self.matches(Self.plainStyle, line) {
                // The button's own modifier chain: its label above, and the
                // modifiers that follow `.buttonStyle` below.
                let window = lines[max(0, i - 8)...min(lines.count - 1, i + 3)].filter { !Self.isComment($0) }
                if window.contains(where: { Self.matches(Self.accentColour, $0) }) {
                    hits.append("\(file):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))")
                }
            }
        }
        #expect(hits.isEmpty, "a hand-built accent button — use .dsPrimary or .dsLink:\n\(hits.joined(separator: "\n"))")
    }

    @Test("the allowlist names only files that exist")
    func allowlistIsCurrent() {
        for file in Array(Self.allowlist.keys) + Array(Self.allowedFolders.keys) {
            #expect(FileManager.default.fileExists(atPath: Self.sourceRoot.appendingPathComponent(file).path),
                    "\(file) is allowlisted but gone — drop it from the allowlist")
        }
    }
}
