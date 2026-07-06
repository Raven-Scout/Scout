import Foundation

/// A structural block of a markdown document together with the exact source
/// line range it occupies. Powers in-place block editing: editing a segment
/// rewrites only `lineStart...lineEnd` of the source, leaving everything else
/// byte-identical (so the plugin's structured tokens are never disturbed).
nonisolated struct KBDocSegment: Identifiable, Equatable {
    enum Kind: Equatable {
        case heading(Int), paragraph, list, quote, code, table, rule, frontmatter
    }

    let kind: Kind
    let lineStart: Int           // inclusive index into the source's line array
    let lineEnd: Int             // inclusive
    let raw: String              // source lines [start...end] joined by "\n"

    // Table-only payload (rendered cells + the source line index of each row).
    var headers: [String] = []
    var rows: [[String]] = []
    var rowLines: [Int] = []

    var id: Int { lineStart }

    // MARK: - Parsing

    static func segments(from source: String) -> [KBDocSegment] {
        let lines = source.components(separatedBy: "\n")
        var segs: [KBDocSegment] = []
        var i = 0

        func make(_ kind: Kind, _ start: Int, _ end: Int) -> KBDocSegment {
            KBDocSegment(kind: kind, lineStart: start, lineEnd: end,
                         raw: lines[start...end].joined(separator: "\n"))
        }

        // Leading frontmatter.
        if lines.first?.trimmingCharacters(in: .whitespaces) == "---" {
            var j = 1
            while j < lines.count, lines[j].trimmingCharacters(in: .whitespaces) != "---" { j += 1 }
            if j < lines.count { segs.append(make(.frontmatter, 0, j)); i = j + 1 }
        }

        while i < lines.count {
            let line = lines[i]
            let t = line.trimmingCharacters(in: .whitespaces)

            if t.isEmpty { i += 1; continue }

            // Fenced code block.
            if t.hasPrefix("```") {
                var j = i + 1
                while j < lines.count, !lines[j].trimmingCharacters(in: .whitespaces).hasPrefix("```") { j += 1 }
                let end = j < lines.count ? j : lines.count - 1
                segs.append(make(.code, i, end)); i = end + 1; continue
            }

            // Table.
            if t.contains("|"), i + 1 < lines.count, KBMarkdownLexer.isTableSeparator(lines[i + 1]) {
                let start = i
                let headers = KBMarkdownLexer.splitRow(line)
                var rows: [[String]] = []
                var rowLines: [Int] = []
                var j = i + 2
                while j < lines.count {
                    let rt = lines[j].trimmingCharacters(in: .whitespaces)
                    if rt.isEmpty || !rt.contains("|") { break }
                    if KBMarkdownLexer.isTableSeparator(lines[j]) { j += 1; continue }
                    var cells = KBMarkdownLexer.splitRow(lines[j])
                    if cells.count < headers.count {
                        cells += Array(repeating: "", count: headers.count - cells.count)
                    } else if cells.count > headers.count {
                        cells = Array(cells.prefix(headers.count))
                    }
                    rows.append(cells); rowLines.append(j); j += 1
                }
                var seg = make(.table, start, j - 1)
                seg.headers = headers; seg.rows = rows; seg.rowLines = rowLines
                segs.append(seg); i = j; continue
            }

            // Horizontal rule.
            if t == "---" || t == "***" || t == "___" { segs.append(make(.rule, i, i)); i += 1; continue }

            // Heading.
            if let h = KBMarkdownLexer.heading(t) {
                segs.append(make(.heading(h.level), i, i)); i += 1; continue
            }

            // Blockquote (consecutive `>` lines).
            if t.hasPrefix(">") {
                var j = i
                while j < lines.count, lines[j].trimmingCharacters(in: .whitespaces).hasPrefix(">") { j += 1 }
                segs.append(make(.quote, i, j - 1)); i = j; continue
            }

            // List item (one line each).
            if KBMarkdownLexer.listItem(line) != nil { segs.append(make(.list, i, i)); i += 1; continue }

            // Paragraph: consecutive "normal" lines.
            var j = i
            while j < lines.count {
                let lt = lines[j].trimmingCharacters(in: .whitespaces)
                if lt.isEmpty || lt.hasPrefix("#") || lt.hasPrefix(">") || lt.hasPrefix("```")
                    || lt == "---" || lt == "***" || lt == "___" { break }
                if KBMarkdownLexer.listItem(lines[j]) != nil { break }
                if lt.contains("|"), j + 1 < lines.count, KBMarkdownLexer.isTableSeparator(lines[j + 1]) { break }
                j += 1
            }
            segs.append(make(.paragraph, i, j - 1)); i = max(j, i + 1)
        }
        return segs
    }

    // MARK: - Partition (collapse leading frontmatter + changelog)

    /// Split parsed segments into a leading title (first H1), the contiguous
    /// frontmatter/changelog segments that head the document (collapsed into
    /// the "History & properties" disclosure), and the rest.
    static func partition(_ segs: [KBDocSegment]) -> (title: KBDocSegment?, history: [KBDocSegment], rest: [KBDocSegment]) {
        var title: KBDocSegment? = nil
        var history: [KBDocSegment] = []
        var rest: [KBDocSegment] = []
        var leading = true
        for seg in segs {
            if leading {
                if title == nil, history.isEmpty, case .heading(let lvl) = seg.kind, lvl == 1 {
                    title = seg; continue
                }
                if seg.kind == .frontmatter || isMetadata(seg) { history.append(seg); continue }
                leading = false
            }
            rest.append(seg)
        }
        return (title, history, rest)
    }

    /// A paragraph segment is "metadata" when it's the file's changelog/parent
    /// header (`**Last updated:**`, `**Prev:**`, `**Parent:**`).
    static func isMetadata(_ seg: KBDocSegment) -> Bool {
        guard seg.kind == .paragraph else { return false }
        let t = seg.raw.trimmingCharacters(in: .whitespaces)
        return t.hasPrefix("**Last updated:**")
            || t.hasPrefix("**Prev:**")
            || t.hasPrefix("**Parent:**")
    }

    // MARK: - Splicing

    /// Replace source lines `start...end` with `newText` (which may be multi-line).
    static func replaceLines(in source: String, start: Int, end: Int, with newText: String) -> String {
        var lines = source.components(separatedBy: "\n")
        guard start >= 0, end < lines.count, start <= end else { return source }
        lines.replaceSubrange(start...end, with: newText.components(separatedBy: "\n"))
        return lines.joined(separator: "\n")
    }

    /// Rewrite a single table cell on `sourceLine`, re-escaping `|` so the pipe
    /// stays cell content (not a column break) — matching how the KB writes
    /// `[[people\|Alias]]` inside cells.
    static func replaceCell(in source: String, sourceLine: Int, col: Int, value: String) -> String {
        var lines = source.components(separatedBy: "\n")
        guard sourceLine >= 0, sourceLine < lines.count else { return source }
        var cells = KBMarkdownLexer.splitRow(lines[sourceLine])
        guard col < cells.count else { return source }
        cells[col] = value.trimmingCharacters(in: .whitespaces)
        let escaped = cells.map { $0.replacingOccurrences(of: "|", with: "\\|") }
        lines[sourceLine] = "| " + escaped.joined(separator: " | ") + " |"
        return lines.joined(separator: "\n")
    }
}
