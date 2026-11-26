import Foundation

/// One comment bound to a task: a `  - author: text` sub-bullet, a quote line
/// (``  > author (2026-04-18 10:20 AM ET): text``) or an Obsidian inline
/// `//==<< text >>==//` note.
nonisolated struct TaskComment: Equatable, Hashable, Sendable {
    let author: String
    /// Free-form timestamp as written in the file. May be empty.
    let timestamp: String
    let text: String
    /// The 1-based `--index` that `scoutctl action-items edit-comment` /
    /// `delete-comment` reach this comment at, or nil when they can't reach
    /// it: a quote line, an inline note, a comment after a blank line or
    /// under a child task. The parser works it out the way the engine's
    /// `list_comment_lines` does (see `EngineCommentIndex`). A comment
    /// without one is shown but never offered for edit or delete: a guessed
    /// index can land on another line.
    let engineIndex: Int?

    init(author: String, timestamp: String, text: String, engineIndex: Int? = nil) {
        self.author = author
        self.timestamp = timestamp
        self.text = text
        self.engineIndex = engineIndex
    }
}

/// Follows the engine's comment lister (`list_comment_lines` in
/// `plugin/engine/scout/action_items/_common.py`) line by line, so the app
/// sends the `--index` the engine will resolve and no other.
///
/// The lister starts under a task line and numbers every
/// `  - <author>: <text>` sub-bullet whose author is not `snoozed-until` or a
/// board field key. `/scout-plan` marker lines are numbered too, for every
/// app version. It ends at a blank line or a line indented no deeper than the
/// task, and skips a child task's own sub-bullets.
nonisolated struct EngineCommentIndex {
    /// 1-based line of the task the open sub-list belongs to; nil when closed.
    private(set) var ownerLine: Int?
    private var ownerIndent = 0
    /// Indent of the child task whose sub-bullets are being skipped, if any.
    private var childIndent: Int?
    private var count = 0

    private static let taskLineRe = try! NSRegularExpression(pattern: #"^\s*-\s+\[[ xX]\]"#)
    private static let commentRe = try! NSRegularExpression(
        pattern: #"^\s+-\s+([A-Za-z][A-Za-z0-9._-]*)\s*:\s*.+?\s*$"#
    )
    private static let nonCommentAuthors = TaskFieldsParser.keys.union(["snoozed-until"])

    /// Feed every line of the file, in order, before the parser handles it.
    /// Returns the index the engine gives this line under `ownerLine`'s task,
    /// or nil when the engine does not list it.
    mutating func advance(_ line: String) -> Int? {
        guard ownerLine != nil else { return nil }
        let indent = line.prefix { $0 == " " || $0 == "\t" }.count
        let blank = line.allSatisfy { $0.isWhitespace }
        if blank || indent <= ownerIndent {
            ownerLine = nil
            return nil
        }
        let ns = line as NSString
        let range = NSRange(location: 0, length: ns.length)
        if Self.taskLineRe.firstMatch(in: line, range: range) != nil {
            if childIndent == nil || indent <= childIndent! { childIndent = indent }
            return nil
        }
        if let child = childIndent {
            if indent > child { return nil }
            childIndent = nil
        }
        guard let m = Self.commentRe.firstMatch(in: line, range: range),
              !Self.nonCommentAuthors.contains(ns.substring(with: m.range(at: 1)).lowercased()) else { return nil }
        count += 1
        return count
    }

    /// The parser read a task at 1-based `lineNumber`. It owns the sub-list
    /// that follows unless one is still open, which makes it a child task:
    /// the engine cannot address a child's comments.
    mutating func sawTask(lineNumber: Int, line: String) {
        guard ownerLine == nil else { return }
        ownerLine = lineNumber
        ownerIndent = line.prefix { $0 == " " || $0 == "\t" }.count
        childIndent = nil
        count = 0
    }
}
