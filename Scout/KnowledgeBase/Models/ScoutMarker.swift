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
}
