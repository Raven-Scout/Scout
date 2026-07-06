import Foundation

/// Line-level markdown lexing shared by the segment parser (`KBDocSegment`)
/// and the KB views. Pure functions over single lines — the one place the KB
/// decides what counts as a heading, list item, or table row.
nonisolated enum KBMarkdownLexer {
    /// `# Heading` → level (1–6) and text, or nil. Requires a space after the
    /// hashes, so `#NoSpace` and 7+ hashes are plain text.
    static func heading(_ trimmed: String) -> (level: Int, text: String)? {
        guard trimmed.hasPrefix("#") else { return nil }
        var level = 0
        for ch in trimmed { if ch == "#" { level += 1 } else { break } }
        guard level >= 1, level <= 6 else { return nil }
        let rest = trimmed.dropFirst(level)
        guard rest.first == " " else { return nil }
        return (level, rest.trimmingCharacters(in: .whitespaces))
    }

    /// `- item` / `1. item` → indentation depth (tabs, or 2 spaces = 1 level),
    /// the ordinal (`"1."`, or nil for bullets) and the item text; nil when the
    /// line isn't a list item.
    static func listItem(_ line: String) -> (depth: Int, ordinal: String?, text: String)? {
        let leading = line.prefix { $0 == " " || $0 == "\t" }
        let depth = leading.reduce(0) { $0 + ($1 == "\t" ? 1 : 0) } + (leading.filter { $0 == " " }.count / 2)
        let content = line[leading.endIndex...]

        if let first = content.first, "-*+".contains(first),
           content.dropFirst().first == " " {
            return (depth, nil, content.dropFirst(2).trimmingCharacters(in: .whitespaces))
        }
        let digits = content.prefix { $0.isNumber }
        if !digits.isEmpty {
            let afterDigits = content[digits.endIndex...]
            if afterDigits.first == ".", afterDigits.dropFirst().first == " " {
                return (depth, "\(digits).", afterDigits.dropFirst(2).trimmingCharacters(in: .whitespaces))
            }
        }
        return nil
    }

    /// A `|---|:--:|` row: only pipes, dashes, colons and spaces, with at least
    /// one dash. Distinguishes a table separator from a `---` horizontal rule
    /// (which has no pipe).
    static func isTableSeparator(_ line: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard t.contains("-"), t.contains("|") else { return false }
        return t.allSatisfy { "|-: ".contains($0) }
    }

    /// Split one table row into trimmed cells, honoring `\|` escapes (the KB uses
    /// them inside wikilinks like `[[people\|Alias]]`) and dropping the empty
    /// cells produced by leading/trailing pipes.
    static func splitRow(_ line: String) -> [String] {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        var cells: [String] = []
        var current = ""
        var prevBackslash = false
        for ch in trimmed {
            if ch == "|" {
                if prevBackslash {
                    current.removeLast()   // drop the escaping backslash
                    current.append("|")
                    prevBackslash = false
                } else {
                    cells.append(current); current = ""
                }
                continue
            }
            current.append(ch)
            prevBackslash = (ch == "\\")
        }
        cells.append(current)
        if cells.first?.trimmingCharacters(in: .whitespaces).isEmpty == true { cells.removeFirst() }
        if cells.last?.trimmingCharacters(in: .whitespaces).isEmpty == true { cells.removeLast() }
        return cells.map { $0.trimmingCharacters(in: .whitespaces) }
    }

    /// Shared heading point sizes for every KB renderer (read mode and the live
    /// editor), so the two modes don't drift apart.
    static func headingSize(_ level: Int) -> CGFloat {
        switch level {
        case 1: return 23
        case 2: return 18.5
        case 3: return 16
        default: return 14.5
        }
    }
}
