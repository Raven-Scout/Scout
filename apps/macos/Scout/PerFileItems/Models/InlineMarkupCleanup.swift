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
    private static let mathRes: [(NSRegularExpression, requiresCommand: Bool)] = [
        (try! NSRegularExpression(pattern: #"\$\$(.+?)\$\$"#), false),
        (try! NSRegularExpression(pattern: #"\\\((.+?)\\\)"#), true),
        (try! NSRegularExpression(pattern: #"\\\[(.+?)\\\]"#), true),
        (try! NSRegularExpression(pattern: #"(?<![\\$])\$(?=[^\s$])([^$\n]*?[^\s$\\])\$(?!\d)"#), true),
    ]
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

    private static func plainMath(_ text: String) -> String {
        var s = text
        for (re, requiresCommand) in mathRes {
            s = replace(re, in: s) { content in
                if requiresCommand && !matches(commandRe, content) { return nil }
                return mathAsText(content)
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
        let ns = s as NSString
        var out = s as NSString
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)).reversed() {
            let group = m.numberOfRanges > 1 ? m.range(at: 1) : m.range
            guard let replacement = transform(ns.substring(with: group)) else { continue }
            out = out.replacingCharacters(in: m.range, with: replacement) as NSString
        }
        return out as String
    }

    private static func replace(_ re: NSRegularExpression, in s: String, groups: Int,
                                _ transform: ([String]) -> String) -> String {
        let ns = s as NSString
        var out = s as NSString
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)).reversed() {
            let values = (1...groups).map { ns.substring(with: m.range(at: $0)) }
            out = out.replacingCharacters(in: m.range, with: transform(values)) as NSString
        }
        return out as String
    }
}
