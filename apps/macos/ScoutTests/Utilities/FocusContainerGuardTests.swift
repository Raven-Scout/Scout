import Foundation
import Testing
@testable import Scout

/// UI audit T4: a button style's `isFocused` is never the button's own focus,
/// only that of a focusable view around it, so a list made `.focusable()`
/// around its buttons rang every one of them, and its `.focusEffectDisabled()`
/// took the system ring off the one Tab was on. Lists take focus through
/// `.dsFocusContainer($focus)`, a sink behind the content, and ring their
/// current item with `.dsCursorRing(…)`. This scans the app's sources so a new
/// list can't bring any of that back quietly.
@Suite("Focusable containers mark themselves")
struct FocusContainerGuardTests {

    /// Each container, and the file that rings its current item.
    static let cursorRings: [String: String] = [
        "ActionItems/ActionItemsView.swift": "ActionItems/Views/TaskCardView.swift",
        "Schedules/SchedulesMasterTable.swift": "Schedules/SchedulesMasterTable.swift",
        "Schedules/SchedulesMasterCards.swift": "Schedules/SchedulesMasterCards.swift",
        "KnowledgeBase/Views/KBTreeView.swift": "KnowledgeBase/Views/KBTreeView.swift",
    ]

    /// Files with a `.focusable()` of their own, and why.
    static let allowlist: [String: String] = [
        // The sink behind every container.
        "Utilities/DSButtonStyle.swift": "dsFocusContainer itself",
        // The focus sits on the disclosure chevron itself, which holds no buttons.
        "ActionItems/TopicBoard/DisclosureRow.swift": "chevron, not a container",
    ]

    static let focusable = try! NSRegularExpression(pattern: #"\.focusable\("#)
    static let container = try! NSRegularExpression(pattern: #"\.dsFocusContainer\("#)
    static let styleFocus = try! NSRegularExpression(pattern: #"Environment\(\\\.isFocused\)"#)

    private func sources() throws -> [(String, [String])] {
        let root = ButtonStyleGuardTests.sourceRoot
        let enumerator = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        var files: [(String, [String])] = []
        for case let url as URL in enumerator where url.pathExtension == "swift" {
            let relative = String(url.path.dropFirst(root.path.count + 1))
            files.append((relative, try String(contentsOf: url, encoding: .utf8).components(separatedBy: "\n")))
        }
        #expect(files.count > 100, "found only \(files.count) Swift files")
        return files
    }

    private func hits(_ pattern: NSRegularExpression, in files: [(String, [String])]) -> [(file: String, line: String)] {
        files.flatMap { file, lines in
            lines.enumerated()
                .filter { !ButtonStyleGuardTests.isComment($0.element) && ButtonStyleGuardTests.matches(pattern, $0.element) }
                .map { (file, "\(file):\($0.offset + 1): \($0.element.trimmingCharacters(in: .whitespaces))") }
        }
    }

    @Test("a list takes focus through .dsFocusContainer, never a .focusable() around its buttons")
    func noFocusableAroundButtons() throws {
        let files = try sources()
        let stray = hits(Self.focusable, in: files).filter { Self.allowlist[$0.file] == nil }
        #expect(stray.isEmpty, """
            use .dsFocusContainer($focus) instead, or allowlist a non-container here:
            \(stray.map(\.line).joined(separator: "\n"))
            """)
        let containers = Set(hits(Self.container, in: files).map(\.file)).subtracting(["Utilities/DSButtonStyle.swift"])
        // The scan must see the containers it guards, or it proves nothing.
        #expect(containers.count >= 4, "found only \(containers.sorted())")
        // A container whose own focus draws nothing is a silent Tab stop.
        let unringed = containers.subtracting(Self.cursorRings.keys).sorted()
        #expect(unringed.isEmpty, """
            ring the current item with .dsCursorRing(…) and name the file in cursorRings:
            \(unringed.joined(separator: "\n"))
            """)
    }

    @Test("no view rings @Environment(\\.isFocused): in a button style it is never the button's own")
    func noStyleRingsInheritedFocus() throws {
        let found = hits(Self.styleFocus, in: try sources())
        #expect(found.isEmpty, """
            a button's focus is the system ring; a field's is its own @FocusState (dsField):
            \(found.map(\.line).joined(separator: "\n"))
            """)
    }

    @Test("each container's current item draws the ring")
    func everyContainerRingsItsCursor() throws {
        for (container, ringFile) in Self.cursorRings {
            let url = ButtonStyleGuardTests.sourceRoot.appendingPathComponent(ringFile)
            let source = try String(contentsOf: url, encoding: .utf8)
            #expect(source.contains(".dsCursorRing("), "\(container): \(ringFile) draws no .dsCursorRing")
            let containerSource = try String(contentsOf: ButtonStyleGuardTests.sourceRoot.appendingPathComponent(container),
                                             encoding: .utf8)
            #expect(containerSource.contains(".dsFocusContainer("), "\(container) is no longer a container; drop it")
        }
    }

    @Test("a list's arrow keys act only while the list itself has the focus")
    func listKeysAreGated() throws {
        let navigation = try NSRegularExpression(pattern: #"\.keyboardListNavigation\("#)
        var found = 0
        var ungated: [String] = []
        for (file, lines) in try sources() {
            for (i, line) in lines.enumerated()
            where !ButtonStyleGuardTests.isComment(line) && ButtonStyleGuardTests.matches(navigation, line) {
                found += 1
                if !lines[i..<min(lines.count, i + 3)].contains(where: { $0.contains("when:") }) {
                    ungated.append("\(file):\(i + 1)")
                }
            }
        }
        #expect(found >= 3, "found only \(found) keyboardListNavigation calls")
        #expect(ungated.isEmpty, "pass when: <the list's own focus>:\n\(ungated.joined(separator: "\n"))")
    }

    @Test("a focused list rings its current item, else the first")
    func ringedItem() {
        #expect(KeyboardListNavigation.ringed("b", in: ["a", "b"]) == "b")
        // Nothing current (Esc), or the current one filtered out: the first, where ↓ starts.
        #expect(KeyboardListNavigation.ringed(nil, in: ["a", "b"]) == "a")
        #expect(KeyboardListNavigation.ringed("gone", in: ["a", "b"]) == "a")
        #expect(KeyboardListNavigation.target(from: "gone", in: ["a", "b"], move: .next) == "a")
        #expect(KeyboardListNavigation.ringed("a", in: [String]()) == nil)
    }

    @Test("the allowlist names files that still exist")
    func allowlistIsCurrent() {
        for file in Array(Self.allowlist.keys) + Array(Self.cursorRings.keys) + Array(Self.cursorRings.values) {
            let url = ButtonStyleGuardTests.sourceRoot.appendingPathComponent(file)
            #expect(FileManager.default.fileExists(atPath: url.path), "\(file) is gone; drop it from the allowlist")
        }
    }
}
