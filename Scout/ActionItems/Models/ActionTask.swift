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
    /// 4-char Crockford prefix extracted from a `[#XXXX]` marker on the task
    /// line, if present. Mandated by scout-plugin's action-items skill phase
    /// since v0.4+; lets scoutctl identify the task via `--by-id` instead of
    /// the brittle `--subject` substring path. `nil` for legacy unprefixed
    /// lines — those still go through the subject-matching fallback.
    let shortPrefix: String?
    /// Source section kind recorded in the snoozed-until marker
    /// (`  - snoozed-until: YYYY-MM-DD (from-kind: <kind>)`). Lets the
    /// renderer keep an urgent task visually urgent after it carries forward
    /// into the target day's `🛌 Snoozed` section, which the section header
    /// alone would render as `.neutral`.
    let snoozedFromKind: ActionSection.Kind?

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
        indentLevel: Int = 0,
        shortPrefix: String? = nil,
        snoozedFromKind: ActionSection.Kind? = nil
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
        self.shortPrefix = shortPrefix
        self.snoozedFromKind = snoozedFromKind
    }

    /// Shortest reliable substring scoutctl's `--subject` matcher can use to
    /// identify this task in the source markdown. scoutctl matches via
    /// `by_subject.lower() in raw_line.lower()` — i.e., against the raw
    /// markdown including `[label](url)` and `[[wikilinks]]`. So the match
    /// key must also be raw — stripping markdown on the app side guarantees
    /// the substring won't appear verbatim in the source line.
    ///
    /// Convention in Scout-written action items is `**<bold subject>**
    /// _(<italic body>)_`; the bold portion is the identity. Take it raw.
    /// For unstyled lines, fall back to plainSubject trimmed at the first
    /// known body separator.
    ///
    /// v0.5.3 quick fix for issue #10; v0.5.4 stops stripping inner markdown
    /// after seeing the same failure mode on tasks containing `[PR #X](url)`
    /// links inside the bold subject.
    var matchableSubject: String {
        if let bold = Self.firstBoldRun(in: subject), !bold.isEmpty {
            return bold
        }
        return Self.trimAtBodySeparator(plainSubject)
    }

    private static func firstBoldRun(in raw: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: #"\*\*(.+?)\*\*"#) else { return nil }
        let range = NSRange(raw.startIndex..., in: raw)
        guard let m = re.firstMatch(in: raw, range: range),
              let r = Range(m.range(at: 1), in: raw) else { return nil }
        return String(raw[r])
    }

    private static func trimAtBodySeparator(_ s: String) -> String {
        // Common separators Scout uses between the subject head and its body.
        // Order matters — italic-open `_(` is the most common in real action
        // items today, so check it first.
        for sep in [" _(", " — ", " – "] {
            if let r = s.range(of: sep) {
                return String(s[..<r.lowerBound])
            }
        }
        return s
    }
}
