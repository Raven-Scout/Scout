import Foundation

/// Text for places that show words, not markup (UI audit T3).
///
/// Two jobs, and neither ever makes a link:
///
/// - `strippingMarkdown(_:)` turns note or log markdown into plain text, for
///   places where rendering it makes no sense: a one-line "Linked from"
///   snippet, a run log's last lines, a card title. Bold, italics, code
///   spans, links, wikilinks, headings, quotes and table pipes go; their
///   words stay.
/// - `codeSpans(_:)` shows the engine's own sentences (Health, banners), which
///   name commands and log lines in backticks: the code is set in monospace,
///   the backticks go, and nothing else is parsed. Engine text can quote run
///   logs and note names, so it never goes through the markdown parser or
///   link detection.
nonisolated enum PlainText {

    // MARK: - Markdown to plain text

    /// `text` without its markdown, line by line (the line count only drops
    /// for a table's `|---|` rule).
    static func strippingMarkdown(_ text: String) -> String {
        guard text.contains(where: { markupCharacters.contains($0) }) else { return text }
        return text.components(separatedBy: "\n").compactMap(stripLine).joined(separator: "\n")
    }

    private static let markupCharacters: Set<Character> = ["*", "_", "`", "[", "]", "|", "#", ">", "\\", "<", "~"]

    private static func stripLine(_ line: String) -> String? {
        if matches(tableRuleRe, line) { return nil }
        var s = line
        // Block prefixes: quote markers, a heading's hashes, a task box.
        s = replace(quotePrefixRe, in: s) { _ in "" }
        s = replace(headingPrefixRe, in: s) { _ in "" }
        s = replace(taskPrefixRe, in: s) { _ in "- " }
        s = replace(starBulletRe, in: s) { _ in "- " }
        // Wikilinks before table cells: an alias written in a table escapes its pipe (`[[a\|b]]`).
        s = replace(wikilinkRe, in: s, groups: 2) { g in wikilinkLabel(target: g[0], alias: g[1]) }
        if isTableRow(s) {
            let cells = splitCells(s).map { inline($0).trimmingCharacters(in: .whitespaces) }
            return cells.filter { !$0.isEmpty }.joined(separator: " · ")
        }
        return inline(s)
    }

    /// Inline markup in one line, code spans kept as their text.
    private static func inline(_ text: String) -> String {
        segments(text).map { seg in
            seg.isCode ? codeText(seg.text) : stripInline(seg.text)
        }.joined()
    }

    private static func stripInline(_ text: String) -> String {
        // Unbalanced bold and LaTeX-style math first, by the cards' own rules.
        var s = InlineMarkupCleanup.clean(text)
        s = replace(imageRe, in: s) { $0 }
        s = replace(linkRe, in: s) { $0 }
        s = replace(autolinkRe, in: s) { $0 }
        s = replace(htmlBreakRe, in: s) { _ in " " }
        s = replace(boldRe, in: s) { $0 }
        s = replace(strikeRe, in: s) { $0 }
        s = replace(starItalicRe, in: s) { $0 }
        s = replace(underscoreItalicRe, in: s) { $0 }
        // A wikilink cut off by a snippet's length limit, or a stray `**` the
        // cleanup kept: neither reads as anything but noise in plain text.
        s = s.replacingOccurrences(of: "[[", with: "").replacingOccurrences(of: "]]", with: "")
        s = replace(strayBoldRe, in: s) { _ in "" }
        return replace(escapeRe, in: s) { $0 }
    }

    /// `[[folder/note|alias]]` reads as its alias, else the note's name.
    private static func wikilinkLabel(target: String, alias: String) -> String {
        if !alias.isEmpty { return alias.replacingOccurrences(of: "\\|", with: "|") }
        let name = target.split(separator: "#").first.map(String.init) ?? target
        let last = name.split(separator: "/").last.map(String.init) ?? name
        return last.hasSuffix(".md") ? String(last.dropLast(3)) : last
    }

    // MARK: Tables

    private static func isTableRow(_ line: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        return t.hasPrefix("|") && t.dropFirst().contains("|")
    }

    /// The cells of a table row, split at unescaped pipes outside code spans.
    private static func splitCells(_ line: String) -> [String] {
        var t = line.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("|") { t.removeFirst() }
        if t.hasSuffix("|") && !t.hasSuffix("\\|") { t.removeLast() }
        var cells: [String] = []
        var current = ""
        for seg in segments(t) {
            if seg.isCode { current += seg.text; continue }
            var previous: Character = " "
            for ch in seg.text {
                if ch == "|" && previous != "\\" {
                    cells.append(current)
                    current = ""
                } else {
                    current.append(ch)
                }
                previous = ch
            }
        }
        cells.append(current)
        return cells
    }

    // MARK: - Code spans for engine text

    /// `text` with each backtick code span marked as code (SwiftUI's `Text`
    /// sets it in the monospaced form of the surrounding font) and its
    /// backticks gone. Nothing else is parsed: no links, no emphasis.
    static func codeSpans(_ text: String) -> AttributedString {
        guard text.contains("`") else { return AttributedString(text) }
        var out = AttributedString()
        for seg in segments(text) {
            guard seg.isCode else {
                out.append(AttributedString(seg.text))
                continue
            }
            var code = AttributedString(codeText(seg.text))
            code.inlinePresentationIntent = .code
            out.append(code)
        }
        return out
    }

    /// The text a viewer reads from `codeSpans(_:)`: for accessibility labels, copy and tests.
    static func codeSpansAsText(_ text: String) -> String {
        String(codeSpans(text).characters)
    }

    /// A code span's text: its backtick run and, per CommonMark, one space
    /// padding each side when both are there.
    private static func codeText(_ span: String) -> String {
        let fence = span.prefix(while: { $0 == "`" }).count
        var inner = String(span.dropFirst(fence).dropLast(fence))
        if inner.count >= 2, inner.hasPrefix(" "), inner.hasSuffix(" "),
           inner.contains(where: { $0 != " " }) {
            inner = String(inner.dropFirst().dropLast())
        }
        return inner
    }

    // MARK: - Counted nouns

    /// `n` and the noun that agrees with it: "1 warning", "2 warnings", never "warning(s)".
    static func count(_ n: Int, _ singular: String, _ plural: String? = nil) -> String {
        "\(n) \(n == 1 ? singular : (plural ?? singular + "s"))"
    }

    // MARK: - Code-span segments

    private struct Segment {
        let text: String
        let isCode: Bool
    }

    /// A run of backticks, its text, and the same run closing it. The same
    /// rule `InlineMarkdownText` and `InlineMarkupCleanup` use.
    private static let codeSpanRe = try! NSRegularExpression(pattern: #"(`+)[^`\n].*?\1"#)

    /// `text` split into code spans (kept whole, backticks included) and the
    /// prose between them.
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

    // MARK: - Patterns

    private static let tableRuleRe = try! NSRegularExpression(
        pattern: #"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$|^\s*\|\s*:?-{3,}:?\s*\|\s*$"#
    )
    private static let quotePrefixRe = try! NSRegularExpression(pattern: #"^(\s*>)+\s?"#)
    private static let headingPrefixRe = try! NSRegularExpression(pattern: #"^\s{0,3}#{1,6}\s+"#)
    private static let taskPrefixRe = try! NSRegularExpression(pattern: #"^(\s*)[-*+]\s+\[[ xX]\]\s+"#)
    private static let starBulletRe = try! NSRegularExpression(pattern: #"^(\s*)[*+]\s+"#)
    private static let wikilinkRe = try! NSRegularExpression(pattern: #"\[\[([^\]|\\]+?)(?:\\?\|([^\]]*))?\]\]"#)
    private static let imageRe = try! NSRegularExpression(pattern: #"!\[([^\]]*)\]\([^)\s]*(?:\s+"[^"]*")?\)"#)
    private static let linkRe = try! NSRegularExpression(pattern: #"\[([^\]]+)\]\([^)\s]*(?:\s+"[^"]*")?\)"#)
    private static let autolinkRe = try! NSRegularExpression(pattern: #"<((?:https?|mailto):[^>\s]+)>"#)
    private static let htmlBreakRe = try! NSRegularExpression(pattern: #"<br\s*/?>"#, options: .caseInsensitive)
    // An escaped mark (`\*`, `\_`) neither opens nor closes; `escapeRe` shows it as written.
    private static let boldRe = try! NSRegularExpression(pattern: #"(?<!\\)\*\*(?=\S)(.+?)(?<=[^\s\\])\*\*"#)
    private static let strikeRe = try! NSRegularExpression(pattern: #"~~(?=\S)(.+?)(?<=\S)~~"#)
    private static let starItalicRe = try! NSRegularExpression(
        pattern: #"(?<![\w*\\])\*(?=[^\s*])([^*\n]*?[^\s*\\])\*(?![\w*])"#
    )
    private static let underscoreItalicRe = try! NSRegularExpression(
        pattern: #"(?<![\w\\])_(?=[^\s_])([^_\n]*?[^\s_\\])_(?!\w)"#
    )
    /// A `**` touching a word on one side only: a cut-off bold span. Not a
    /// glob (`a/**/b`), a power (`2**8`) or a kwargs prefix (`f(**opts)`).
    private static let strayBoldRe = try! NSRegularExpression(pattern: #"(?<![\w*/(,=])\*\*(?=\w)|(?<=\w)\*\*(?![\w*/])"#)
    /// A markdown escape: a backslash before ASCII punctuation.
    private static let escapeRe = try! NSRegularExpression(pattern: #"\\([!-/:-@\[-`{-~])"#)

    // MARK: - Regex helpers

    private static func matches(_ re: NSRegularExpression, _ s: String) -> Bool {
        re.firstMatch(in: s, range: NSRange(location: 0, length: (s as NSString).length)) != nil
    }

    /// Replace each match with `transform(group 1, or the whole match)`.
    private static func replace(_ re: NSRegularExpression, in s: String,
                                _ transform: (String) -> String) -> String {
        let ns = s as NSString
        var out = s as NSString
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)).reversed() {
            let group = m.numberOfRanges > 1 && m.range(at: 1).location != NSNotFound ? m.range(at: 1) : m.range
            out = out.replacingCharacters(in: m.range, with: transform(ns.substring(with: group))) as NSString
        }
        return out as String
    }

    private static func replace(_ re: NSRegularExpression, in s: String, groups: Int,
                                _ transform: ([String]) -> String) -> String {
        let ns = s as NSString
        var out = s as NSString
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)).reversed() {
            let values = (1...groups).map { i -> String in
                let r = m.range(at: i)
                return r.location == NSNotFound ? "" : ns.substring(with: r)
            }
            out = out.replacingCharacters(in: m.range, with: transform(values)) as NSString
        }
        return out as String
    }
}
