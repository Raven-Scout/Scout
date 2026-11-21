import Foundation
import Testing
@testable import Scout

/// UI audit T4: a button style's `isFocused` is the nearest focusable
/// ancestor's focus, so while a `.focusable()` list had the keyboard focus every
/// button inside it drew a focus ring. Each focusable container marks itself
/// with `.dsFocusContainer(…)` so the DS styles skip the inherited ring, and
/// rings its current item with `.dsCursorRing(…)` while it holds the focus
/// itself: the hand check found Tab stopping on the Schedules grid with nothing
/// drawn. This scans the app's sources so a new container can't bring either
/// back quietly.
@Suite("Focusable containers mark themselves")
struct FocusContainerGuardTests {

    /// Each container, and the file that rings its current item.
    static let cursorRings: [String: String] = [
        "ActionItems/ActionItemsView.swift": "ActionItems/Views/TaskCardView.swift",
        "Schedules/SchedulesMasterTable.swift": "Schedules/SchedulesMasterTable.swift",
        "Schedules/SchedulesMasterCards.swift": "Schedules/SchedulesMasterCards.swift",
        "KnowledgeBase/Views/KBTreeView.swift": "KnowledgeBase/Views/KBTreeView.swift",
    ]

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
        var containers: Set<String> = []
        for (file, lines) in files {
            for (i, line) in lines.enumerated()
            where !ButtonStyleGuardTests.isComment(line) && ButtonStyleGuardTests.matches(Self.focusable, line) {
                found += 1
                containers.insert(file)
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
        // A container whose own focus draws nothing is a silent Tab stop.
        let unringed = containers.subtracting(Self.cursorRings.keys).sorted()
        #expect(unringed.isEmpty, """
            ring the current item with .dsCursorRing(…) and name the file in cursorRings:
            \(unringed.joined(separator: "\n"))
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
            #expect(containerSource.contains(".focusable("), "\(container) is no longer a container; drop it")
        }
    }

    /// Where a focusable container's items are drawn. A native control there
    /// (a `.borderless` button, a `Menu`) doesn't report its own focus the way
    /// DS styles do, so the card around it kept its ring and the list kept
    /// the arrows while Tab sat on "Show more" (T4 review).
    static let containerContent: [String] = ["ActionItems/", "Schedules/", "KnowledgeBase/Views/KBTreeView.swift"]
    static let nativeControl = try! NSRegularExpression(
        pattern: #"\.buttonStyle\(\.(plain|borderless|link|bordered\w*)\)|\.menuStyle\(|\bLink\(destination"#)

    @Test("a native control drawn inside a focusable container reports its own focus")
    func nativeControlsReportOwnFocus() throws {
        let root = ButtonStyleGuardTests.sourceRoot
        let enumerator = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        var found = 0
        var hits: [String] = []
        for case let url as URL in enumerator where url.pathExtension == "swift" {
            let relative = String(url.path.dropFirst(root.path.count + 1))
            guard Self.containerContent.contains(where: { relative.hasPrefix($0) }) else { continue }
            let lines = try String(contentsOf: url, encoding: .utf8).components(separatedBy: "\n")
            for (i, line) in lines.enumerated()
            where !ButtonStyleGuardTests.isComment(line) && ButtonStyleGuardTests.matches(Self.nativeControl, line) {
                found += 1
                let near = lines[i..<min(lines.count, i + 4)]
                if !near.contains(where: { $0.contains(".dsReportsOwnFocus()") }) {
                    hits.append("\(relative):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))")
                }
            }
        }
        #expect(found >= 10, "found only \(found) native controls; is the scan still looking in the right place?")
        #expect(hits.isEmpty, """
            add .dsReportsOwnFocus() to each (or move it to a DS style):
            \(hits.joined(separator: "\n"))
            """)
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

    /// The rules the DS styles and containers draw by.
    @Test("a control rings only its own focus; a container rings its item only when it holds the focus itself")
    func focusRules() {
        // Keyboard navigation off: a button never holds focus, so inside a focused list it draws nothing.
        #expect(!DSFocus.controlDrawsRing(isFocused: true, isEnabled: true, containerHasFocus: true, keyboardNavigation: false))
        // On: the button's isFocused is its own, so it rings inside a focused container (the hand-check failure).
        #expect(DSFocus.controlDrawsRing(isFocused: true, isEnabled: true, containerHasFocus: true, keyboardNavigation: true))
        #expect(DSFocus.controlDrawsRing(isFocused: true, isEnabled: true, containerHasFocus: false, keyboardNavigation: false))
        #expect(!DSFocus.controlDrawsRing(isFocused: false, isEnabled: true, containerHasFocus: false, keyboardNavigation: true))
        // A disabled control's focus is always inherited.
        #expect(!DSFocus.controlDrawsRing(isFocused: true, isEnabled: false, containerHasFocus: true, keyboardNavigation: true))
        #expect(DSFocus.containerShowsCursor(hasFocus: true, focusInside: false))
        #expect(!DSFocus.containerShowsCursor(hasFocus: true, focusInside: true))
        #expect(!DSFocus.containerShowsCursor(hasFocus: false, focusInside: false))
    }

    @Test("the allowlist names files that still exist")
    func allowlistIsCurrent() {
        for file in Array(Self.allowlist.keys) + Array(Self.cursorRings.keys) + Array(Self.cursorRings.values) {
            let url = ButtonStyleGuardTests.sourceRoot.appendingPathComponent(file)
            #expect(FileManager.default.fileExists(atPath: url.path), "\(file) is gone; drop it from the allowlist")
        }
    }
}
