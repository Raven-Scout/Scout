import Foundation
import SwiftUI
import Testing
@testable import Scout

/// The KB tree's outline keys (UI audit #77).
@Suite("KB tree keyboard")
struct KBTreeKeyboardTests {
    // knowledge-base/
    //   people/        (dir)
    //     alex.md
    //     priya.md
    //   projects.md
    let tree: [KBNode] = [
        Self.dir("knowledge-base/people", [Self.file("knowledge-base/people/alex.md"),
                                           Self.file("knowledge-base/people/priya.md")]),
        Self.file("knowledge-base/projects.md"),
    ]
    let people = "knowledge-base/people"
    let alex = "knowledge-base/people/alex.md"
    let projects = "knowledge-base/projects.md"

    @Test("↓ skips a closed folder's notes and opens the note it lands on")
    func downOverClosedFolder() {
        let out = KBTreeKeyboard.handle(.down, cursor: people, nodes: tree, expanded: [])
        #expect(out.cursor == projects)
        #expect(out.open == projects)
    }

    @Test("landing on a folder doesn't open anything")
    func upToFolder() {
        let out = KBTreeKeyboard.handle(.up, cursor: projects, nodes: tree, expanded: [])
        #expect(out.cursor == people)
        #expect(out.open == nil)
    }

    @Test("→ opens a folder, then steps into it")
    func right() {
        let opened = KBTreeKeyboard.handle(.right, cursor: people, nodes: tree, expanded: [])
        #expect(opened.expanded == [people])
        #expect(opened.cursor == people)
        let stepped = KBTreeKeyboard.handle(.right, cursor: people, nodes: tree, expanded: [people])
        #expect(stepped.cursor == alex)
        #expect(stepped.open == alex)
    }

    @Test("← steps out to the parent, then closes it")
    func left() {
        let out = KBTreeKeyboard.handle(.left, cursor: alex, nodes: tree, expanded: [people])
        #expect(out.cursor == people)
        #expect(out.open == nil)
        let closed = KBTreeKeyboard.handle(.left, cursor: people, nodes: tree, expanded: [people])
        #expect(closed.expanded.isEmpty)
        // A top-level note has nowhere to step out to.
        #expect(!KBTreeKeyboard.handle(.left, cursor: projects, nodes: tree, expanded: []).changed)
    }

    @Test("Return toggles a folder and opens a note")
    func toggle() {
        #expect(KBTreeKeyboard.handle(.toggle, cursor: people, nodes: tree, expanded: []).expanded == [people])
        #expect(KBTreeKeyboard.handle(.toggle, cursor: people, nodes: tree, expanded: [people]).expanded.isEmpty)
        #expect(KBTreeKeyboard.handle(.toggle, cursor: projects, nodes: tree, expanded: []).open == projects)
    }

    @Test("with nothing chosen, ↓ starts at the top; at the bottom ↓ does nothing")
    func edges() {
        #expect(KBTreeKeyboard.handle(.down, cursor: nil, nodes: tree, expanded: []).cursor == people)
        #expect(!KBTreeKeyboard.handle(.down, cursor: projects, nodes: tree, expanded: []).changed)
        #expect(KBTreeKeyboard.handle(.end, cursor: nil, nodes: tree, expanded: [people]).cursor == projects)
    }

    @Test("only bare keys count")
    func keys() {
        #expect(KBTreeKeyboard.Key(.downArrow, modifiers: []) == .down)
        #expect(KBTreeKeyboard.Key(.return, modifiers: []) == .toggle)
        #expect(KBTreeKeyboard.Key(.downArrow, modifiers: .command) == nil)
        #expect(KBTreeKeyboard.Key("a", modifiers: []) == nil)
    }

    private static func file(_ rel: String) -> KBNode {
        KBNode(kind: .file, url: URL(fileURLWithPath: "/vault/\(rel)"), relativePath: rel,
               name: (rel as NSString).lastPathComponent, ext: "md", children: [])
    }

    private static func dir(_ rel: String, _ children: [KBNode]) -> KBNode {
        KBNode(kind: .directory, url: URL(fileURLWithPath: "/vault/\(rel)"), relativePath: rel,
               name: (rel as NSString).lastPathComponent, ext: "", children: children)
    }
}
