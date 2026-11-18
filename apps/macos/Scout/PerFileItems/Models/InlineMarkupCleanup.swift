import Foundation

/// Rewrites inline markup that `InlineMarkdownText` can't render, so a card
/// reads as text instead of showing raw symbols (UI audit R5). Research cards
/// on a real vault showed:
///
/// - LaTeX-style math, `\(\ge 1.5\)`, which the markdown parser turned into
///   `(\ge 1.5)` (it reads `\(` as an escaped parenthesis);
/// - an unbalanced `**` left over from a cut-off bold span.
///
/// Math becomes plain text (`≥ 1.5`). A `**` that can't open or close bold
/// (spaces on both sides) is dropped, and so is the last one of an odd count.
/// Inline code spans are left exactly as written: `**kwargs` or `\ge` inside
/// backticks is code, not markup.
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

    /// A run of backticks, its text, and the same run closing it.
    private static let codeSpanRe = try! NSRegularExpression(pattern: #"(`+)[^`\n].*?\1"#)

    private static func segments(_ text: String) -> [Segment] {
        let ns = text as NSString
        var out: [Segment] = []
        var cursor = 0
        for m in codeSpanRe.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            if m.range.location > cursor {
                out.append(Segment(text: ns.substring(with: NSRange(location: cursor, length: m.range.location - cursor)),
                                   isCode: false))
            }
            out.append(Segment(text: ns.substring(with: m.range), isCode: true))
            cursor = m.range.location + m.range.length
        }
        if cursor < ns.length { out.append(Segment(text: ns.substring(from: cursor), isCode: false)) }
        return out
    }

    // MARK: - Math

    /// `$$…$$` is always math. `\(…\)`, `\[…\]` and `$…$` count only when they
    /// hold a `\command`, so prices ("$5 and $6") and escaped brackets stay.
    private static let mathRes: [(NSRegularExpression, requiresCommand: Bool)] = [
        (try! NSRegularExpression(pattern: #"\$\$(.+?)\$\$"#), false),
        (try! NSRegularExpression(pattern: #"\\\((.+?)\\\)"#), true),
        (try! NSRegularExpression(pattern: #"\\\[(.+?)\\\]"#), true),
        (try! NSRegularExpression(pattern: #"(?<![\\$])\$([^$\n]+?)\$"#), true),
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

    /// Drop a `**` with whitespace (or the text's edge) on both sides: it can
    /// neither open nor close bold, so the parser would show it. Then, if the
    /// rest still don't pair up, drop the last one.
    private static func balanceBold(_ parts: [Segment]) -> [Segment] {
        var parts = parts
        var marks: [(segment: Int, location: Int)] = []
        for (i, part) in parts.enumerated() where !part.isCode {
            let ns = part.text as NSString
            for m in boldMarkRe.matches(in: part.text, range: NSRange(location: 0, length: ns.length)) {
                marks.append((i, m.range.location))
            }
        }
        guard !marks.isEmpty else { return parts }

        func isSpace(_ ch: Character?) -> Bool { ch == nil || ch!.isWhitespace }
        let joined = parts.map(\.text).joined()
        var offsets: [Int] = []  // segment start offsets in UTF-16
        var running = 0
        for part in parts { offsets.append(running); running += (part.text as NSString).length }
        let all = joined as NSString
        func char(at utf16: Int) -> Character? {
            guard utf16 >= 0, utf16 < all.length else { return nil }
            return Character(all.substring(with: all.rangeOfComposedCharacterSequence(at: utf16)))
        }

        var drop: [(segment: Int, location: Int)] = []
        var kept: [(segment: Int, location: Int)] = []
        for mark in marks {
            let at = offsets[mark.segment] + mark.location
            if isSpace(char(at: at - 1)) && isSpace(char(at: at + 2)) { drop.append(mark) } else { kept.append(mark) }
        }
        if kept.count % 2 == 1, let last = kept.last { drop.append(last) }

        // Remove right to left within each segment so earlier locations hold.
        for mark in drop.sorted(by: { ($0.segment, $0.location) > ($1.segment, $1.location) }) {
            let ns = parts[mark.segment].text as NSString
            var replaced = ns.replacingCharacters(in: NSRange(location: mark.location, length: 2), with: "")
            // "a ** b" → "a b", not "a  b".
            let before = mark.location - 1
            if before >= 0, before < (replaced as NSString).length,
               (replaced as NSString).substring(with: NSRange(location: before, length: 1)) == " ",
               mark.location < (replaced as NSString).length,
               (replaced as NSString).substring(with: NSRange(location: mark.location, length: 1)) == " " {
                replaced = (replaced as NSString).replacingCharacters(in: NSRange(location: before, length: 1), with: "")
            }
            parts[mark.segment].text = replaced
        }
        return parts
    }

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
