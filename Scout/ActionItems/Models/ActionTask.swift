import Foundation

struct ActionTask: Identifiable, Equatable, Hashable, Sendable {
    /// Ephemeral; regenerated on each parse. Do not persist.
    let id: UUID
    /// 1-based line number in the source file (for diagnostics).
    let lineNumber: Int
    let done: Bool
    /// Raw markdown subject (with `**bold**`, `[[wikilinks]]`, etc.).
    let subject: String
    /// Markdown-stripped subject. MUST match the Python CLIs'
    /// ``_strip_markdown_tokens`` output byte-for-byte.
    let plainSubject: String
    /// Post-dash/colon remainder. May be empty.
    let body: String
    let comments: [TaskComment]
    let deepLinks: [TaskDeepLink]
    /// Parsed from a `— 🛌 Snoozed until YYYY-MM-DD` body suffix. ``nil`` otherwise.
    let snoozedUntil: Date?
    /// Parsed from a `_(carried in from YYYY-MM-DD)_` body marker. ``nil`` otherwise.
    let carriedInFrom: Date?
    /// Markdown-list nesting depth. ``0`` = top-level, ``1`` = child of the
    /// preceding top-level task, etc. Computed from the leading whitespace on
    /// the source line (1 tab = 1 level; otherwise 2 spaces = 1 level).
    let indentLevel: Int

    init(
        id: UUID,
        lineNumber: Int,
        done: Bool,
        subject: String,
        plainSubject: String,
        body: String,
        comments: [TaskComment],
        deepLinks: [TaskDeepLink],
        snoozedUntil: Date?,
        carriedInFrom: Date?,
        indentLevel: Int = 0
    ) {
        self.id = id
        self.lineNumber = lineNumber
        self.done = done
        self.subject = subject
        self.plainSubject = plainSubject
        self.body = body
        self.comments = comments
        self.deepLinks = deepLinks
        self.snoozedUntil = snoozedUntil
        self.carriedInFrom = carriedInFrom
        self.indentLevel = indentLevel
    }
}
