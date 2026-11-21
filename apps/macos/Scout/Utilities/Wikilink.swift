import Foundation

/// The one rule for what counts as a `[[wikilink]]`.
///
/// `[[target]]` or `[[target|alias]]`, where the alias separator may be
/// written `\|`, the form notes use inside table cells. Neither part holds a
/// newline (LF), and the target holds no `[`, `]` or `|`. The alias may hold
/// a balanced `[…]` with no brackets inside it (`[[alex|Alex [ext]]]`), which
/// renders as written, but no other bracket. So an unclosed `[[` gives up at
/// the next `[[`, unmatched bracket or newline instead of scanning on.
///
/// `InlineMarkdownText` renders exactly these as links, and the knowledge
/// base counts exactly these as links and backlinks
/// (`KnowledgeBaseService.extractWikilinks`, `KBLinkRewrite`), as do a task's
/// trailing link chips (`TaskBodyBlock`) and its `Refs:` line
/// (`ActionItemsParser.parseRefs`). Before, only the renderer refused `[`
/// and newlines, so a note could show a backlink that never rendered as a link.
nonisolated enum Wikilink {
    /// What a target may hold.
    static let targetCharacter = #"[^\[\]|\n]"#
    /// One piece of an alias: a character that isn't a bracket or a newline,
    /// or a bracketed run of them. Each piece starts with a different
    /// character from the others, and the run can't hold `]`, so matching
    /// stays linear.
    static let aliasPiece = #"(?:[^\[\]\n]|\[[^\[\]\n]*\])"#
    /// The alias separator, `|` or `\|`.
    static let separator = #"\\?\|"#

    /// Group 1 is the target, group 2 the alias (absent without one).
    static let pattern = #"\[\["# + "(\(targetCharacter)+?)(?:\(separator)(\(aliasPiece)+))?" + #"\]\]"#

    /// The same link with no groups, to embed in a larger pattern.
    static let ungroupedPattern = #"\[\["# + "\(targetCharacter)+?(?:\(separator)\(aliasPiece)+)?" + #"\]\]"#

    /// `pattern`, compiled once.
    static let regex = try! NSRegularExpression(pattern: pattern)

    struct Match: Equatable {
        /// The whole `[[…]]`, in UTF-16 units.
        let range: NSRange
        /// The target as written.
        let target: String
        /// The alias as written (a table cell's `\|` inside it is kept), or nil.
        let alias: String?
    }

    /// Every wikilink in `text`, in order.
    static func matches(in text: String) -> [Match] {
        let ns = text as NSString
        return regex.matches(in: text, range: NSRange(location: 0, length: ns.length)).map { match(in: ns, $0) }
    }

    /// `text`, if it is a single wikilink and nothing else.
    static func whole(_ text: String) -> Match? {
        let ns = text as NSString
        let full = NSRange(location: 0, length: ns.length)
        guard let m = regex.firstMatch(in: text, range: full), m.range == full else { return nil }
        return match(in: ns, m)
    }

    private static func match(in ns: NSString, _ m: NSTextCheckingResult) -> Match {
        let alias = m.range(at: 2)
        return Match(range: m.range,
                     target: ns.substring(with: m.range(at: 1)),
                     alias: alias.location == NSNotFound ? nil : ns.substring(with: alias))
    }
}
