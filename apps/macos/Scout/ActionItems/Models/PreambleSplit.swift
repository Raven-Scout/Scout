import Foundation

/// Splits one preamble paragraph of a daily action-items file into the
/// headline an update card always shows and the body behind its chevron.
nonisolated enum PreambleSplit {
    /// A leading `**…**` bold span is the headline; everything after it is the
    /// body. Without one, the headline is the first sentence: text up to a `.`
    /// or `:` followed by a space (or the end). A colon inside a time or a dot
    /// inside a version stays put, so "Rewritten by the 09:03 run: …" keeps
    /// "09:03" in its headline (UI audit R5). So do an ellipsis ("Wait… ok",
    /// "Wait... ok") and the dot of a common abbreviation ("e.g. ", "vs. ").
    /// Leading quote markers (`> `, `> [!note]`) are not part of the text.
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
            if ch == "." || ch == ":", next == s.endIndex || s[next].isWhitespace,
               !(ch == "." && (endsEllipsis(s, at: idx) || endsAbbreviation(s, at: idx))) {
                let head = String(s[..<idx])
                let body = String(s[next...]).trimmingCharacters(in: .whitespaces)
                return (head, body)
            }
            idx = next
        }
        return (s, "")
    }

    /// "e.g.", "vs.", "approx." — lowercased, with their final dot.
    private static let abbreviations: Set<String> = [
        "e.g.", "i.e.", "etc.", "vs.", "cf.", "approx.", "incl.", "esp.", "a.m.", "p.m.",
        "mr.", "mrs.", "ms.", "dr.", "fig.", "jan.", "feb.", "aug.", "sept.", "oct.", "nov.", "dec.",
    ]

    /// The dot at `idx` closes a run of dots ("...").
    private static func endsEllipsis(_ s: String, at idx: String.Index) -> Bool {
        idx > s.startIndex && s[s.index(before: idx)] == "."
    }

    /// The word ending with the dot at `idx` is a common abbreviation.
    private static func endsAbbreviation(_ s: String, at idx: String.Index) -> Bool {
        var start = idx
        while start > s.startIndex, !s[s.index(before: start)].isWhitespace, s[s.index(before: start)] != "(" {
            start = s.index(before: start)
        }
        return abbreviations.contains(s[start...idx].lowercased())
    }

    /// `> `, `> > `, then an optional callout marker `[!kind]` (with `+`/`-`).
    private static let markerRe = try! NSRegularExpression(pattern: #"^(?:>[ \t]?)+(?:\[![A-Za-z][\w-]*\][+-]?[ \t]*)?"#)

    private static func stripQuoteMarkers(_ s: String) -> String {
        let ns = s as NSString
        guard let m = markerRe.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { return s }
        return ns.substring(from: m.range.length).trimmingCharacters(in: .whitespaces)
    }
}
