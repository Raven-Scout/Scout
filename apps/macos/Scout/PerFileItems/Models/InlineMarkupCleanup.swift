import Foundation

/// Rewrites inline markup that `InlineMarkdownText` can't render, so a card
/// reads as text instead of showing raw symbols (UI audit R5). Research cards
/// on a real vault showed:
///
/// - LaTeX-style math, `\(\ge 1.5\)`, which the markdown parser turned into
///   `(\ge 1.5)` (it reads `\(` as an escaped parenthesis);
/// - an unbalanced `**` left over from a cut-off bold span.
///
/// Math becomes plain text (`≥ 1.5`). The `**` marks are paired the way the
/// markdown parser pairs them, and each one that pairs with nothing is
/// dropped, wherever it is, but only when it reads as a cut-off bold span: it
/// touches a word on exactly one side, and nothing next to it suggests a glob
/// (`src/**/*.swift`), an operator (`x ** 2`), a kwargs prefix
/// (`f(**kwargs)`) or a URL. Anything else is left as written. Inline code
/// spans are never touched.
nonisolated enum InlineMarkupCleanup {
    static func clean(_ text: String) -> String {
        guard text.contains("\\") || text.contains("$") || text.contains("**") else { return text }
        let parts = segments(text)
        // Math first, segment by segment: a math span never crosses a code span.
        let mathed = parts.map { $0.isCode ? $0 : Segment(text: plainMath($0.text), isCode: false) }
        return balanceBold(mathed).map(\.text).joined()
    }

    // MARK: - Code spans

    private struct Segment {
        var text: String
        let isCode: Bool
    }

    /// A run of backticks, its text, and the same run closing it (`CodeSpanScanner`).
    private static func segments(_ text: String) -> [Segment] {
        let ns = text as NSString
        var out: [Segment] = []
        var cursor = 0
        for m in CodeSpanScanner.ranges(in: text) {
            if m.location > cursor {
                out.append(Segment(text: ns.substring(with: NSRange(location: cursor, length: m.location - cursor)),
                                   isCode: false))
            }
            out.append(Segment(text: ns.substring(with: m), isCode: true))
            cursor = m.location + m.length
        }
        if cursor < ns.length { out.append(Segment(text: ns.substring(from: cursor), isCode: false)) }
        return out
    }

    // MARK: - Math

    /// `$$…$$` is always math. `\(…\)`, `\[…\]` and `$…$` count only when they
    /// hold a `\command`, so escaped brackets stay. `$…$` also follows
    /// Pandoc's rule, so prices stay: the opening `$` is followed by a
    /// non-space, the closing `$` follows a non-space and isn't followed by a
    /// digit ("$5 (p \ge 0.1) or $10" is not math).
    ///
    /// The three bracketed forms are found by `delimitedSpans`, in linear
    /// time. They used to be the regexes `\$\$(.+?)\$\$`, `\\\((.+?)\\\)` and
    /// `\\\[(.+?)\\\]`, which scanned to the end of the line from every
    /// opener that never closed: 5,000 unclosed `\(` took a third of a second
    /// in a release build, longer on non-ASCII text. `$…$` stays a regex: its
    /// body can't cross a `$`, so each attempt stops at the next one.
    private static let mathForms: [(MathForm, requiresCommand: Bool)] = [
        (.delimited(open: "$$", close: "$$"), false),
        (.delimited(open: #"\("#, close: #"\)"#), true),
        (.delimited(open: #"\["#, close: #"\]"#), true),
        (.regex(try! NSRegularExpression(pattern: #"(?<![\\$])\$(?=[^\s$])([^$\n]*?[^\s$\\])\$(?!\d)"#)), true),
    ]

    private enum MathForm {
        /// A two-character opener, at least one character on the same line,
        /// and the first two-character closer after it.
        case delimited(open: String, close: String)
        /// Group 1 is the content.
        case regex(NSRegularExpression)
    }

    /// The spans the regex `open(.+?)close` finds, for a two-character `open`
    /// and `close`, in one pass: each opener, then the first closer that
    /// starts at least one character after it, if no line break comes first.
    /// A failed opener costs O(1), because the next closer and the next line
    /// break from every position are worked out once, from the right.
    /// `whole` includes the delimiters; `content` is what's between them.
    static func delimitedSpans(in text: String, open: String, close: String) -> [(whole: NSRange, content: NSRange)] {
        let o = Array(open.utf16), c = Array(close.utf16)
        precondition(o.count == 2 && c.count == 2, "two-character delimiters only")
        let u = Array(text.utf16)
        let n = u.count
        guard n >= 5 else { return [] }

        // nextClose[i]: the first index >= i where `close` starts, or n.
        // nextBreak[i]: the first index >= i holding a line break, or n.
        var nextClose = [Int](repeating: n, count: n + 1)
        var nextBreak = [Int](repeating: n, count: n + 1)
        var i = n - 1
        while i >= 0 {
            nextClose[i] = i + 1 < n && u[i] == c[0] && u[i + 1] == c[1] ? i : nextClose[i + 1]
            nextBreak[i] = isLineBreak(u[i]) ? i : nextBreak[i + 1]
            i -= 1
        }

        var out: [(whole: NSRange, content: NSRange)] = []
        var p = 0
        while p + 4 < n {
            guard u[p] == o[0], u[p + 1] == o[1] else { p += 1; continue }
            // The content is at least one character, so a closer starts at p + 3 or later.
            let q = nextClose[p + 3]
            guard q < n, nextBreak[p + 2] >= q else { p += 1; continue }
            out.append((NSRange(location: p, length: q + 2 - p), NSRange(location: p + 2, length: q - p - 2)))
            p = q + 2
        }
        return out
    }

    /// The characters a regex `.` doesn't match (ICU's line terminators), as
    /// in `CodeSpanScanner`.
    private static func isLineBreak(_ c: UInt16) -> Bool {
        switch c {
        case 0x0A, 0x0B, 0x0C, 0x0D, 0x85, 0x2028, 0x2029: return true
        default: return false
        }
    }

    private static let commandRe = try! NSRegularExpression(pattern: #"\\[A-Za-z]+"#)

    /// Symbol commands, by name. Used inside math and, bare, in prose.
    static let symbols: [String: String] = [
        "ge": "≥", "geq": "≥", "le": "≤", "leq": "≤", "ne": "≠", "neq": "≠",
        "approx": "≈", "sim": "~", "times": "×", "cdot": "·", "div": "÷", "pm": "±",
        "to": "→", "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒", "infty": "∞",
        "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "Delta": "Δ", "epsilon": "ε",
        "lambda": "λ", "mu": "μ", "pi": "π", "sigma": "σ", "Sigma": "Σ", "theta": "θ",
        "sum": "Σ", "sqrt": "√", "degree": "°",
    ]

    /// A bare symbol command in prose (`p \ge 0.05`), not followed by a letter,
    /// so `\new` is not `\ne`.
    private static let bareSymbolRe = try! NSRegularExpression(
        pattern: #"\\("# + symbols.keys.sorted { $0.count > $1.count }.joined(separator: "|") + #")(?![A-Za-z])"#
    )

    /// Each math form in turn, then bare symbols. Internal so tests can pin
    /// it against the regexes it replaced.
    static func plainMath(_ text: String) -> String {
        var s = text
        for (form, requiresCommand) in mathForms {
            let transform: (String) -> String? = { content in
                if requiresCommand && !matches(commandRe, content) { return nil }
                return mathAsText(content)
            }
            switch form {
            case let .delimited(open, close):
                s = replace(delimitedSpans(in: s, open: open, close: close), in: s, transform)
            case let .regex(re):
                s = replace(re, in: s, transform)
            }
        }
        return replace(bareSymbolRe, in: s) { name in symbols[name] }
    }

    private static let wrapperRe = try! NSRegularExpression(
        pattern: #"\\(?:text|textbf|textit|mathrm|mathbf|mathit|operatorname)\{([^{}]*)\}"#
    )
    private static let fracRe = try! NSRegularExpression(pattern: #"\\frac\{([^{}]*)\}\{([^{}]*)\}"#)

    /// The content of a math span as plain text: `\frac{a}{b}` is `a/b`,
    /// `\text{x}` is `x`, known symbols are their character, any other
    /// command loses its backslash, braces go. Markdown-significant characters
    /// are escaped so the inline renderer shows them as written.
    static func mathAsText(_ content: String) -> String {
        var s = content
        s = replace(wrapperRe, in: s) { $0 }
        s = replace(fracRe, in: s, groups: 2) { "\($0[0])/\($0[1])" }
        for spacing in [#"\,"#, #"\;"#, #"\:"#, #"\!"#, #"\ "#, #"\quad"#] {
            s = s.replacingOccurrences(of: spacing, with: " ")
        }
        s = s.replacingOccurrences(of: #"\%"#, with: "%").replacingOccurrences(of: #"\$"#, with: "$")
        s = replace(commandRe, in: s) { command in
            let name = String(command.dropFirst())
            return symbols[name] ?? name
        }
        s = s.replacingOccurrences(of: "{", with: "").replacingOccurrences(of: "}", with: "")
        s = s.split(separator: " ", omittingEmptySubsequences: true).joined(separator: " ")
        var escaped = ""
        for ch in s {
            if "*_[]`".contains(ch) { escaped.append("\\") }
            escaped.append(ch)
        }
        return escaped
    }

    // MARK: - Bold

    /// Exactly two asterisks, not part of a longer run (`***` is left alone).
    private static let boldMarkRe = try! NSRegularExpression(pattern: #"(?<!\*)\*\*(?!\*)"#)

    /// Drop each `**` (outside code) that pairs with nothing, if it reads as a
    /// cut-off bold span. Marks pair the way the markdown parser pairs them: a
    /// mark with text after it can open, one with text before it can close,
    /// and a closer takes the nearest open mark. So in `**A and **B**` the
    /// first mark is the stray one, not the last (UI audit T3). A mark with
    /// spaces on both sides can do neither, so it doesn't count.
    private static func balanceBold(_ parts: [Segment]) -> [Segment] {
        var parts = parts
        let joined = parts.map(\.text).joined() as NSString
        var marks: [(segment: Int, location: Int, absolute: Int)] = []
        var offset = 0
        for (i, part) in parts.enumerated() {
            let ns = part.text as NSString
            if !part.isCode {
                for m in boldMarkRe.matches(in: part.text, range: NSRange(location: 0, length: ns.length)) {
                    marks.append((i, m.range.location, offset + m.range.location))
                }
            }
            offset += ns.length
        }
        func unit(_ at: Int) -> Character? {
            guard at >= 0, at < joined.length else { return nil }
            return Character(joined.substring(with: joined.rangeOfComposedCharacterSequence(at: at)))
        }
        func isSpace(_ ch: Character?) -> Bool { ch.map(\.isWhitespace) ?? true }
        let pairing = marks.filter { !(isSpace(unit($0.absolute - 1)) && isSpace(unit($0.absolute + 2))) }
        var open: [Int] = []       // indexes into `pairing`
        var unpaired: [Int] = []
        for (i, mark) in pairing.enumerated() {
            let canClose = !isSpace(unit(mark.absolute - 1))
            let canOpen = !isSpace(unit(mark.absolute + 2))
            if canClose, !open.isEmpty {
                open.removeLast()
            } else if canOpen {
                open.append(i)
            } else {
                unpaired.append(i)
            }
        }
        unpaired += open
        // Last first, so each removal leaves the earlier marks' offsets as they were.
        for i in unpaired.sorted(by: >) {
            let mark = pairing[i]
            guard isCutOffBold(at: mark.absolute, in: joined, unit: unit) else { continue }
            let ns = parts[mark.segment].text as NSString
            parts[mark.segment].text = ns.replacingCharacters(in: NSRange(location: mark.location, length: 2), with: "")
        }
        return parts
    }

    /// A `**` at `at` that touches a word on exactly one side, with no `/`,
    /// digit, `(`, `,`, `=` or `*` beside it, not inside a URL or a link
    /// target, and not a kwargs prefix (`**kwargs)`, `**opts,`).
    private static func isCutOffBold(at: Int, in text: NSString, unit: (Int) -> Character?) -> Bool {
        let before = unit(at - 1), after = unit(at + 2)
        func isWord(_ ch: Character?) -> Bool { ch.map { $0.isLetter || $0.isNumber } ?? false }
        guard isWord(before) != isWord(after) else { return false }
        let blocked: (Character?) -> Bool = { ch in
            guard let ch else { return false }
            return ch.isNumber || "/(,=*".contains(ch)
        }
        if blocked(before) || blocked(after) { return false }
        // The whitespace-delimited token holding the mark: a URL or a link
        // target (`](…)`) keeps its asterisks.
        var start = at, end = at + 2
        while start > 0, !isSpace(unit(start - 1)) { start -= 1 }
        while end < text.length, !isSpace(unit(end)) { end += 1 }
        let token = text.substring(with: NSRange(location: start, length: end - start))
        if token.contains("://") || token.contains("](") { return false }
        return !looksLikeKwargs(text, markAt: at)
    }

    /// Whether the `**` at `at` reads as Python's keyword-argument unpacking,
    /// not a cut-off bold span. The name after it is followed by `)` or `,`
    /// (`f(**opts)`), is snake_case (`**extra_fields`), is `kwargs`, `kwds`
    /// or `kw`, ends the text as another lower-case name kwargs go by
    /// (`with **opts`), or a `*args` comes before it (`*a and **kw`). Names
    /// compare case-sensitively: "**Params changed" and "**options for the
    /// migration" are cut-off bold.
    static func looksLikeKwargs(_ text: NSString, markAt at: Int) -> Bool {
        let start = at + 2
        guard start < text.length else { return false }
        let ahead = text.substring(with: NSRange(location: start, length: min(80, text.length - start)))
        guard let m = identifierRe.firstMatch(in: ahead, range: NSRange(location: 0, length: (ahead as NSString).length))
        else { return false }
        if m.range(at: 2).location != NSNotFound { return true }
        let name = (ahead as NSString).substring(with: m.range(at: 1))
        if name.contains("_") || kwargsOnlyNames.contains(name) { return true }
        let endsText = start + m.range.length == text.length
        if endsText, kwargsNames.contains(name) { return true }
        let back = max(0, at - 200)
        let before = text.substring(with: NSRange(location: back, length: at - back))
        return starArgsRe.firstMatch(in: before, range: NSRange(location: 0, length: (before as NSString).length)) != nil
    }

    /// A name, and the `)` or `,` that would end it as an argument.
    private static let identifierRe = try! NSRegularExpression(pattern: #"^([A-Za-z_][A-Za-z0-9_]*)([),])?"#)
    /// Names only kwargs go by, and English words kwargs often go by too.
    private static let kwargsOnlyNames: Set<String> = ["kwargs", "kwds", "kw"]
    private static let kwargsNames: Set<String> = ["opts", "options", "params", "attrs", "extra"]
    /// `*args`: a lone star before a name. Not an italic span, `*word*`,
    /// `*Note:*` or `*e.g.*`: a single `*` closing it later on the line
    /// (within 40 characters, and not half of a `**`) rules it out.
    private static let starArgsRe = try! NSRegularExpression(
        pattern: #"(?<![\w*])\*[A-Za-z_][A-Za-z0-9_]*(?![\w*])(?![^*\n]{0,40}(?<!\*)\*(?!\*))"#
    )

    private static func isSpace(_ ch: Character?) -> Bool { ch.map(\.isWhitespace) ?? true }

    // MARK: - Regex helpers

    private static func matches(_ re: NSRegularExpression, _ s: String) -> Bool {
        re.firstMatch(in: s, range: NSRange(location: 0, length: (s as NSString).length)) != nil
    }

    /// Replace each match of `re` with `transform(group 1, or the whole match
    /// when the pattern has no group)`; a nil result keeps the match as is.
    private static func replace(_ re: NSRegularExpression, in s: String,
                                _ transform: (String) -> String?) -> String {
        let spans = re.matches(in: s, range: NSRange(location: 0, length: (s as NSString).length)).map {
            (whole: $0.range, content: $0.numberOfRanges > 1 ? $0.range(at: 1) : $0.range)
        }
        return replace(spans, in: s, transform)
    }

    private static func replace(_ re: NSRegularExpression, in s: String, groups: Int,
                                _ transform: ([String]) -> String) -> String {
        let ns = s as NSString
        return rebuild(s, re.matches(in: s, range: NSRange(location: 0, length: ns.length)).map { m in
            (m.range, transform((1...groups).map { ns.substring(with: m.range(at: $0)) }))
        })
    }

    /// Replace each span with `transform(its content)`; a nil result keeps it as is.
    private static func replace(_ spans: [(whole: NSRange, content: NSRange)], in s: String,
                                _ transform: (String) -> String?) -> String {
        let ns = s as NSString
        return rebuild(s, spans.compactMap { span in
            transform(ns.substring(with: span.content)).map { (span.whole, $0) }
        })
    }

    /// `s` with each range, in order and not overlapping, swapped for its
    /// text, built front to back in one pass. (Splicing each one into the
    /// whole string, last first, copied the string once per match.)
    private static func rebuild(_ s: String, _ edits: [(NSRange, String)]) -> String {
        guard !edits.isEmpty else { return s }
        let ns = s as NSString
        var out = ""
        out.reserveCapacity(ns.length)
        var cursor = 0
        for (range, text) in edits {
            out += ns.substring(with: NSRange(location: cursor, length: range.location - cursor))
            out += text
            cursor = range.location + range.length
        }
        out += ns.substring(from: cursor)
        return out
    }
}
