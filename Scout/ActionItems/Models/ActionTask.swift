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
    /// identify this task. scoutctl matches via
    /// `by_subject.lower() in item.title.lower()` where `item.title` is the
    /// **cleaned** title — `[#XXXX]` prefix removed, `**` removed, status
    /// emoji at start removed, priority emoji removed anywhere, and
    /// strikethrough unwrapped (see `engine/scout/action_items/parser.py`).
    /// So Scout's needle has to mirror those same cleanups, otherwise we
    /// send a substring that exists in the raw markdown but not in the title.
    ///
    /// Convention in Scout-written action items is `**<bold subject>**
    /// _(<italic body>)_`; the bold portion is the identity. We extract it
    /// (which already drops the `**` markers since the regex captures
    /// inside), then apply the same emoji + strikethrough cleanups scoutctl
    /// does. For unstyled lines, fall back to plainSubject trimmed at the
    /// first known body separator.
    ///
    /// v0.5.3/v0.5.4 chased subject-matching failures by tweaking what we
    /// kept raw; v0.6.0 instead aligns with scout-plugin commit 3071486
    /// which moved subject-matching off `raw_line` and onto the cleaned
    /// title. The `🔴` in `**🔴 Merge …**` was the trigger.
    var matchableSubject: String {
        let raw: String
        if let bold = Self.firstBoldRun(in: subject), !bold.isEmpty {
            raw = bold
        } else {
            raw = Self.trimAtBodySeparator(plainSubject)
        }
        return Self.cleanForScoutctlMatch(raw)
    }

    private static func firstBoldRun(in raw: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: #"\*\*(.+?)\*\*"#) else { return nil }
        let range = NSRange(raw.startIndex..., in: raw)
        guard let m = re.firstMatch(in: raw, range: range),
              let r = Range(m.range(at: 1), in: raw) else { return nil }
        return String(raw[r])
    }

    /// Apply the same cleanups scoutctl's parser applies to the title field
    /// it matches `--subject` against. Keep in sync with
    /// `engine/scout/action_items/parser.py`.
    private static func cleanForScoutctlMatch(_ s: String) -> String {
        // STRIKETHROUGH: `~~text~~` → `text` (scoutctl regex `~~(.+?)~~`).
        var out = s
        if let re = try? NSRegularExpression(pattern: #"~~(.+?)~~"#) {
            let mutableOut = NSMutableString(string: out)
            re.replaceMatches(
                in: mutableOut,
                range: NSRange(location: 0, length: mutableOut.length),
                withTemplate: "$1"
            )
            out = mutableOut as String
        }
        // PRIORITY_EMOJI: 🔴 / 🟡 / 🟢 removed anywhere.
        for emoji in ["🔴", "🟡", "🟢"] {
            out = out.replacingOccurrences(of: emoji, with: "")
        }
        // STATUS_EMOJI: ✅ / 🔄 / ❓ / ⬜ removed only at start (after trim).
        out = out.trimmingCharacters(in: .whitespacesAndNewlines)
        for emoji in ["✅", "🔄", "❓", "⬜"] where out.hasPrefix(emoji) {
            out = String(out.dropFirst(emoji.count))
            break
        }
        // Collapse double spaces left behind by the emoji removals so the
        // substring matches even when scoutctl's title has its own collapse.
        while out.contains("  ") {
            out = out.replacingOccurrences(of: "  ", with: " ")
        }
        return out.trimmingCharacters(in: .whitespacesAndNewlines)
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
