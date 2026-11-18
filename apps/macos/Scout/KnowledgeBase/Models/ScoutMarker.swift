import Foundation

/// The inline feedback marker the Scout dreaming session reads. The user leaves
/// `//==<< comment >>==//` at a spot in a note; dreaming's per-location pass
/// (`rg -F '//==<<'`) acts on it and strips it when resolved. This type is the
/// single source of truth for the syntax on the app side (the Action Items
/// markdown parser recognizes the same form).
nonisolated enum ScoutMarker {
    static let open = "//==<<"
    static let close = ">>==//"

    /// Wrap `text` as a single-line marker. Internal newlines collapse to
    /// spaces (the contract is line-oriented — every reader scans line by
    /// line). Returns `nil` for empty / whitespace-only input.
    static func format(_ text: String) -> String? {
        let collapsed = text
            .components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
            .joined(separator: " ")
        guard !collapsed.isEmpty else { return nil }
        return "\(open) \(collapsed) \(close)"
    }

    /// True if `line`, trimmed, is a standalone marker line.
    static func isMarkerLine(_ line: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        return t.hasPrefix(open) && t.hasSuffix(close) && t.count >= open.count + close.count
    }

    /// The comment text inside a marker line, or `nil` if `line` isn't one.
    static func body(of line: String) -> String? {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard isMarkerLine(t) else { return nil }
        return t.dropFirst(open.count).dropLast(close.count)
            .trimmingCharacters(in: .whitespaces)
    }

    // MARK: - Comments on a passage

    /// Longest quoted passage a marker carries, in characters. Enough to find
    /// the passage again; the comment itself is never shortened.
    static let maxQuoteLength = 140

    /// A marker about one passage of the note: `//==<< on "passage": comment >>==//`.
    /// The quote is what the reader highlighted, so dreaming knows which words
    /// the comment is about, and the reader can highlight them again. It is
    /// flattened to one line, its double quotes turned to single ones (so the
    /// closing quote is unambiguous), and cut to `maxQuoteLength` with an
    /// ellipsis. An empty quote gives a plain marker.
    static func format(_ text: String, quote: String?) -> String? {
        guard let comment = format(text).map({ body(of: $0) ?? "" }), !comment.isEmpty else { return nil }
        let flat = (quote ?? "")
            .components(separatedBy: .whitespacesAndNewlines)
            .filter { !$0.isEmpty }
            .joined(separator: " ")
            .replacingOccurrences(of: "\"", with: "'")
            .replacingOccurrences(of: "\u{201C}", with: "'")
            .replacingOccurrences(of: "\u{201D}", with: "'")
            .replacingOccurrences(of: close, with: "")
            .replacingOccurrences(of: open, with: "")
        guard !flat.isEmpty else { return "\(open) \(comment) \(close)" }
        let clipped = flat.count > maxQuoteLength
            ? String(flat.prefix(maxQuoteLength)).trimmingCharacters(in: .whitespaces) + "…"
            : flat
        return "\(open) on \"\(clipped)\": \(comment) \(close)"
    }

    /// A marker's parts: the quoted passage (if it names one) and the comment.
    static func parts(of marker: String) -> (quote: String?, comment: String)? {
        guard let inner = body(of: marker) else { return nil }
        guard inner.hasPrefix("on \""),
              let end = inner.range(of: "\": ", range: inner.index(inner.startIndex, offsetBy: 4)..<inner.endIndex)
        else { return (nil, inner) }
        let quote = String(inner[inner.index(inner.startIndex, offsetBy: 4)..<end.lowerBound])
        let comment = String(inner[end.upperBound...]).trimmingCharacters(in: .whitespaces)
        return (quote.isEmpty ? nil : quote, comment)
    }
}
