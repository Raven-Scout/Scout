import Foundation
import Testing
@testable import Scout

/// The app writes a comment as `  - <handle>: <text>` through `scoutctl
/// add-comment --author`. Both parsers accept only `[A-Za-z][A-Za-z0-9._-]*`
/// as the author, so the name from Settings is folded into that shape.
@Suite("Comment author")
struct CommentAuthorTests {
    static let handles: [(String, String)] = [
        ("alex", "alex"),
        ("  priya  ", "priya"),
        ("Alex Rivera", "Alex-Rivera"),
        ("Zoë", "Zoe"),
        ("o'priya", "opriya"),
        ("42sam", "sam"),
        ("sam.dev_2", "sam.dev_2"),
        ("", "user"),
        ("!!!", "user"),
        // Letters that are not accented forms, and other scripts, are
        // transliterated rather than dropped.
        ("Priya Łaz", "Priya-Laz"),
        ("Sam Øby", "Sam-Oby"),
        ("Straße", "Strasse"),
        ("Ælex", "AElex"),
        ("李雷", "li-lei"),
        ("😀", "user"),
        // A run of spaces and dashes is one separator.
        ("Alex - Rivera", "Alex-Rivera"),
    ]

    @Test(arguments: handles)
    func nameBecomesAHandle(_ name: String, _ expected: String) {
        #expect(CommentAuthor.handle(name) == expected)
    }

    /// What the app writes, its own parser reads back with the same author,
    /// instead of `scout` with the name glued onto the text.
    @Test(arguments: ["alex", "Alex Rivera", "Zoë", "Priya Łaz", "李雷"])
    func aWrittenCommentParsesBackWithItsAuthor(_ name: String) throws {
        let handle = CommentAuthor.handle(name)
        let md = """
        # Action Items 2026-06-15

        ## 🔴 Urgent

        - [ ] [#IOTA] **Reply to Priya about the RFC**
          - \(handle): looked into it
        """
        let doc = try ActionItemsParser.parse(
            text: md,
            sourceURL: URL(fileURLWithPath: "/tmp/action-items-2026-06-15.md"),
            sourceBytes: md.utf8.count
        )
        let task = try #require(doc.sections.first { $0.kind == .urgent }?.tasks.first)
        #expect(task.comments.map(\.author) == [handle])
        #expect(task.comments.map(\.text) == ["looked into it"])
    }

    @Test func ownCommentsAreRecognizedByTheirHandle() {
        #expect(CommentAuthor.isOwn(commentAuthor: "Alex-Rivera", userName: "Alex Rivera"))
        #expect(CommentAuthor.isOwn(commentAuthor: "user", userName: ""))
        #expect(!CommentAuthor.isOwn(commentAuthor: "scout", userName: "Alex Rivera"))
    }

    /// Inline `//==<< … >>==//` comments carry the raw Settings name, not the
    /// handle, and are still the user's own.
    @Test func inlineCommentsByTheRawNameAreOwn() {
        #expect(CommentAuthor.isOwn(commentAuthor: "alex rivera", userName: "Alex Rivera"))
        #expect(!CommentAuthor.isOwn(commentAuthor: "priya", userName: "Alex Rivera"))
    }
}
