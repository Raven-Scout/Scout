import Foundation
import Testing

/// UI audit T4: a button style's `isFocused` is the nearest focusable
/// ancestor's focus, so while a `.focusable()` list had the keyboard focus every
/// button inside it drew a focus ring. Each focusable container marks itself
/// with `.dsFocusContainer(…)` so the DS styles skip the inherited ring. This
/// scans the app's sources so a new container can't bring the bug back quietly.
@Suite("Focusable containers mark themselves")
struct FocusContainerGuardTests {

    /// Files whose `.focusable()` isn't on a container of buttons, and why.
    static let allowlist: [String: String] = [
        // The focus sits on the disclosure chevron itself, which holds no buttons.
        "ActionItems/TopicBoard/DisclosureRow.swift": "chevron, not a container",
    ]

    static let focusable = try! NSRegularExpression(pattern: #"\.focusable\("#)
    /// How far below `.focusable()` the marker may sit (after `.focused` and `.focusEffectDisabled`).
    static let window = 6

    @Test("every .focusable() container is followed by .dsFocusContainer")
    func everyContainerIsMarked() throws {
        // Every source file, not the button guard's list: its allowlist is about button styles.
        let root = ButtonStyleGuardTests.sourceRoot
        let enumerator = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        var files: [(String, [String])] = []
        for case let url as URL in enumerator where url.pathExtension == "swift" {
            let relative = String(url.path.dropFirst(root.path.count + 1))
            guard Self.allowlist[relative] == nil else { continue }
            files.append((relative, try String(contentsOf: url, encoding: .utf8).components(separatedBy: "\n")))
        }
        #expect(files.count > 100, "found only \(files.count) Swift files")
        var found = 0
        var hits: [String] = []
        for (file, lines) in files {
            for (i, line) in lines.enumerated()
            where !ButtonStyleGuardTests.isComment(line) && ButtonStyleGuardTests.matches(Self.focusable, line) {
                found += 1
                let below = lines[i..<min(lines.count, i + 1 + Self.window)]
                if !below.contains(where: { $0.contains(".dsFocusContainer(") }) {
                    hits.append("\(file):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))")
                }
            }
        }
        // The scan must see the containers it guards, or it proves nothing.
        #expect(found >= 4, "found only \(found) .focusable() calls")
        #expect(hits.isEmpty, """
            add .dsFocusContainer(<its focus>) under each, or allowlist a non-container here:
            \(hits.joined(separator: "\n"))
            """)
    }

    @Test("the allowlist names files that still exist")
    func allowlistIsCurrent() {
        for file in Self.allowlist.keys {
            let url = ButtonStyleGuardTests.sourceRoot.appendingPathComponent(file)
            #expect(FileManager.default.fileExists(atPath: url.path), "\(file) is gone; drop it from the allowlist")
        }
    }
}
