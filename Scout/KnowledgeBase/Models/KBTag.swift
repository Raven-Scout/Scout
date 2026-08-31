import Foundation

/// Recognizes the vault's semantic `[#TAG]` mnemonics so they can render as
/// chips and be clicked to find every note that mentions them.
///
/// Two forms are recognized, and both render identically (the brackets are
/// presentation, not meaning):
///
/// - **Bracketed** `[#SLBETA]` — the form the plugin writes on action items.
/// - **Bare** `#SLBETA` — the form that dominates prose in research notes.
///
/// A tag is 2–8 `[A-Z0-9]` characters containing **at least one letter**. The
/// letter requirement is what keeps tags and GitHub refs disjoint: a numeric
/// `#123` is never a tag, so `GitHubRefLinkifier` still owns it and the two
/// rewriters can run over the same text without fighting. It's the same rule
/// `ActionItemsWriter.shortPrefix` uses to reject `[#555]`.
///
/// Tags are emitted as `[#TAG](scout-tag://TAG)` — the same trick
/// `InlineMarkdownText` already uses for wikilinks, so clicks arrive through
/// `OpenURLAction` and the run can be styled by its link attribute.
///
/// Matches inside existing markdown links, wikilinks and inline code spans are
/// left untouched, so already-formatted content isn't corrupted.
enum KBTag {
    /// URL scheme for a tag click. Paired with `EnvironmentValues.kbTagHandler`.
    static let scheme = "scout-tag"

    // Compiled once. Like `GitHubRefLinkifier`, this runs for every rendered
    // string in a pass, and NSRegularExpression compilation dominates the cost.

    /// Single alternation so the bracketed form wins over the bare form on the
    /// same token. Group 1 = bracketed body, group 2 = bare body.
    ///
    /// The bare branch is fenced on both sides: `(?<![\w/#])` keeps it out of
    /// `word#TAG`, `##TAG` and `…/page#TAG` URL fragments; `(?![A-Za-z0-9_])`
    /// stops `#KAIRELx` from matching the `#KAIREL` prefix. A trailing `'s` or
    /// `,` is fine — those aren't word characters.
    private static let tagRe = try! NSRegularExpression(
        pattern: #"\[#([A-Z0-9]{2,8})\]|(?<![\w/#])#([A-Z0-9]{2,8})(?![A-Za-z0-9_])"#
    )

    /// Spans that must not be rewritten: markdown links, wikilinks, inline code.
    /// Mirrors `GitHubRefLinkifier.protectedRes`.
    private static let protectedRes: [NSRegularExpression] = [
        #"\[\[[^\]]*\]\]"#,        // [[wikilink]] / [[target|alias]]
        #"\[[^\]]*\]\([^)]*\)"#,   // [text](url)
        #"`[^`]*`"#,               // `inline code`
    ].map { try! NSRegularExpression(pattern: $0) }

    /// Hair spaces padding the chip label, so the background wash doesn't sit
    /// flush against the glyphs. SwiftUI can't round or inset an inline
    /// `backgroundColor` run, so the breathing room has to be real characters.
    private static let pad = "\u{2009}"

    /// `TAG` if `body` is a well-formed tag body, else nil. Exposed so search
    /// can decide whether a typed query is a tag.
    static func normalized(_ body: String) -> String? {
        let t = body.hasPrefix("#") ? String(body.dropFirst()) : body
        guard (2...8).contains(t.count) else { return nil }
        guard t.allSatisfy({ $0.isASCII && ($0.isUppercase || $0.isNumber) }) else { return nil }
        guard t.contains(where: \.isLetter) else { return nil }   // `#555` is a GitHub ref
        return t
    }

    /// Every distinct tag mentioned in `s`, in first-appearance order.
    static func tags(in s: String) -> [String] {
        guard s.utf8.contains(UInt8(ascii: "#")) else { return [] }
        let ns = s as NSString
        let full = NSRange(location: 0, length: ns.length)
        let protectedRanges = self.protectedRanges(in: ns, full: full)
        var seen = Set<String>()
        var out: [String] = []
        for m in tagRe.matches(in: s, range: full) {
            guard !intersectsProtected(m.range, protectedRanges) else { continue }
            guard let body = body(of: m, in: ns), seen.insert(body).inserted else { continue }
            out.append(body)
        }
        return out
    }

    /// Rewrite every tag into `[ #TAG ](scout-tag://TAG)`.
    static func linkify(_ s: String) -> String {
        // Most rendered strings contain no `#` at all. This runs on every cache
        // miss in `InlineMarkdownText`, which is a scroll-visible path, so skip
        // the four regex passes below on a single byte scan when we can.
        guard s.utf8.contains(UInt8(ascii: "#")) else { return s }
        let ns = s as NSString
        let full = NSRange(location: 0, length: ns.length)
        let protectedRanges = self.protectedRanges(in: ns, full: full)

        var result = s
        // Reversed so earlier match ranges stay valid as we splice.
        for m in tagRe.matches(in: s, range: full).reversed() {
            guard !intersectsProtected(m.range, protectedRanges) else { continue }
            guard let body = body(of: m, in: ns) else { continue }
            let replacement = "[\(pad)#\(body)\(pad)](\(scheme)://\(body))"
            result = (result as NSString).replacingCharacters(in: m.range, with: replacement)
        }
        return result
    }

    /// The tag body of a match, from whichever branch of the alternation fired.
    private static func body(of m: NSTextCheckingResult, in ns: NSString) -> String? {
        let range = m.range(at: 1).location != NSNotFound ? m.range(at: 1) : m.range(at: 2)
        guard range.location != NSNotFound else { return nil }
        return normalized(ns.substring(with: range))
    }

    // MARK: - Protected ranges

    private static func protectedRanges(in ns: NSString, full: NSRange) -> [NSRange] {
        protectedRes.flatMap { $0.matches(in: ns as String, range: full).map(\.range) }
    }

    private static func intersectsProtected(_ range: NSRange, _ protectedRanges: [NSRange]) -> Bool {
        for p in protectedRanges where NSIntersectionRange(range, p).length > 0 { return true }
        return false
    }
}
