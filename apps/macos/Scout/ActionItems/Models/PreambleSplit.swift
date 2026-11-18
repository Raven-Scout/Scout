import Foundation

/// Splits one preamble paragraph of a daily action-items file into the
/// headline an update card always shows and the body behind its chevron.
nonisolated enum PreambleSplit {
    /// A leading `**…**` bold span is the headline; everything after it is the
    /// body. Without one, the headline is the first sentence: text up to a `.`
    /// or `:` followed by a space (or the end). A colon inside a time or a dot
    /// inside a version stays put, so "Rewritten by the 09:03 run: …" keeps
    /// "09:03" in its headline (UI audit R5). Leading quote markers
    /// (`> `, `> [!note]`) are not part of the text.
    static func split(_ raw: String) -> (headline: String, body: String) {
        let s = stripQuoteMarkers(raw.trimmingCharacters(in: .whitespaces))
        if s.hasPrefix("**"), let close = s.range(of: "**", range: s.index(s.startIndex, offsetBy: 2)..<s.endIndex) {
            let head = String(s[s.index(s.startIndex, offsetBy: 2)..<close.lowerBound])
            let rest = String(s[close.upperBound...]).trimmingCharacters(in: .whitespaces)
            // Strip a leading period or dash separator so the headline doesn't dangle.
            let cleaned = rest.drop(while: { ".—– ".contains($0) })
            return (head.trimmingCharacters(in: .whitespaces), String(cleaned))
        }
        var idx = s.startIndex
        while idx < s.endIndex {
            let ch = s[idx]
            let next = s.index(after: idx)
            if ch == "." || ch == ":", next == s.endIndex || s[next].isWhitespace {
                let head = String(s[..<idx])
                let body = String(s[next...]).trimmingCharacters(in: .whitespaces)
                return (head, body)
            }
            idx = next
        }
        return (s, "")
    }

    /// `> `, `> > `, then an optional callout marker `[!kind]` (with `+`/`-`).
    private static let markerRe = try! NSRegularExpression(pattern: #"^(?:>[ \t]?)+(?:\[![A-Za-z][\w-]*\][+-]?[ \t]*)?"#)

    private static func stripQuoteMarkers(_ s: String) -> String {
        let ns = s as NSString
        guard let m = markerRe.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { return s }
        return ns.substring(from: m.range.length).trimmingCharacters(in: .whitespaces)
    }
}
