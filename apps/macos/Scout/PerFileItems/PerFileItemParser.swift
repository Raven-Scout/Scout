// Scout/PerFileItems/PerFileItemParser.swift
import Foundation

/// Pure parser for one per-file item (YAML frontmatter + markdown body).
/// Returns nil when the file has no frontmatter (skips index/non-item files).
nonisolated enum PerFileItemParser {
    static func parseFile(contents: String, fileURL: URL) -> PerFileItem? {
        guard let (frontmatter, body) = splitFrontmatter(contents) else { return nil }
        let fields = parseFrontmatterFields(frontmatter)
        // Keyword fields need the value as written: whether it was quoted
        // decides whether a `#` in it is a comment.
        let raw = parseFrontmatterFields(frontmatter, unquote: false)
        let stem = fileURL.deletingPathExtension().lastPathComponent

        let date = keyword(raw["date"]).nonEmpty ?? datePrefix(of: stem) ?? ""
        let title = fields["title"]?.nonEmpty ?? stem
        let status = ItemStatus.parse(keyword(raw["status"]))
        let priority = ItemPriority.parse(keyword(raw["priority"]))
        let source = fields["source"]?.nonEmpty
        let area = fields["area"]?.nonEmpty
        let cleanBody = stripLeadingHeading(body).trimmingCharacters(in: .whitespacesAndNewlines)

        return PerFileItem(fileURL: fileURL, date: date, title: title, status: status,
                           priority: priority, source: source, area: area, bodyMarkdown: cleanBody)
    }

    static func splitFrontmatter(_ text: String) -> (frontmatter: String, body: String)? {
        let lines = text.components(separatedBy: "\n")
        guard let first = lines.first,
              first.trimmingCharacters(in: .whitespaces) == "---" else { return nil }
        var frontmatter: [String] = []
        var i = 1
        while i < lines.count {
            if lines[i].trimmingCharacters(in: .whitespaces) == "---" {
                let body = i + 1 < lines.count ? lines[(i + 1)...].joined(separator: "\n") : ""
                return (frontmatter.joined(separator: "\n"), body)
            }
            frontmatter.append(lines[i])
            i += 1
        }
        return nil
    }

    static func parseFrontmatterFields(_ frontmatter: String, unquote: Bool = true) -> [String: String] {
        var out: [String: String] = [:]
        for line in frontmatter.components(separatedBy: "\n") {
            guard let colon = line.firstIndex(of: ":") else { continue }
            let key = line[..<colon].trimmingCharacters(in: .whitespaces).lowercased()
            guard !key.isEmpty else { continue }
            var value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            if unquote, value.count >= 2, value.hasPrefix("\""), value.hasSuffix("\"") {
                value = String(value.dropFirst().dropLast())
            }
            out[key] = value
        }
        return out
    }

    /// A keyword field's value (`status`, `priority`, `date`), read as YAML
    /// reads a scalar, from the value as written:
    /// - Unquoted: a `#` that opens the value or follows whitespace starts a
    ///   comment. Without this, `status: done   # retired …` parsed as an
    ///   unknown — so active — status (#85).
    /// - Quoted (`"…"` or `'…'`): the value is what's inside the quotes, `#`
    ///   included, and anything after the closing quote is a comment. So
    ///   `"done # pending review"` stays an unknown, active status rather than
    ///   hiding unfinished work as done.
    /// - An unterminated quote is kept verbatim (an unknown, active status).
    ///
    /// Only keyword fields get this. Free text keeps its `#` (`source: Pattern
    /// #197`, `title: C# notes`) — the app has always shown it verbatim, and a
    /// `#` can never be part of a valid keyword. The engine's first-token rule
    /// (`heartbeat.py` `_item_status`) agrees on bare keywords; the
    /// differences are pinned in `PerFileItemParserTests`.
    static func keyword(_ raw: String?) -> String {
        guard let value = raw?.trimmingCharacters(in: .whitespaces) else { return "" }
        if let quote = value.first, quote == "\"" || quote == "'" {
            let inner = value.dropFirst()
            guard let close = inner.firstIndex(of: quote) else { return value }
            return String(inner[..<close])
        }
        var previous: Character = " "
        for index in value.indices {
            if value[index] == "#", previous.isWhitespace {
                return value[..<index].trimmingCharacters(in: .whitespaces)
            }
            previous = value[index]
        }
        return value
    }

    /// The trailing comment on a keyword field's value as written (the text
    /// after `key:`), with the whitespace before its `#`; nil when there is
    /// none. Same rules as `keyword(_:)`: unquoted, a `#` that opens the value
    /// or follows whitespace starts it; quoted, it's what follows the closing
    /// quote, if that is a `#` comment. An unterminated quote has none.
    ///
    /// The writer uses this to carry a comment over when the app rewrites the
    /// field, so `status: open   # why` becomes `status: done   # why`.
    static func keywordComment(_ raw: Substring) -> Substring? {
        let value = raw.drop(while: { $0 == " " || $0 == "\t" })
        if let quote = value.first, quote == "\"" || quote == "'" {
            let inner = value.dropFirst()
            guard let close = inner.firstIndex(of: quote) else { return nil }
            let tail = inner[inner.index(after: close)...]
            return tail.drop(while: \.isWhitespace).first == "#" ? tail : nil
        }
        var previous: Character = " "
        for index in value.indices {
            if value[index] == "#", previous.isWhitespace {
                var start = index
                while start > value.startIndex, value[value.index(before: start)].isWhitespace {
                    start = value.index(before: start)
                }
                return value[start...]
            }
            previous = value[index]
        }
        return nil
    }

    static func stripLeadingHeading(_ body: String) -> String {
        var lines = body.components(separatedBy: "\n")
        while let first = lines.first, first.trimmingCharacters(in: .whitespaces).isEmpty {
            lines.removeFirst()
        }
        if let first = lines.first, first.hasPrefix("# "), !first.hasPrefix("## ") {
            lines.removeFirst()
        }
        return lines.joined(separator: "\n")
    }

    static func datePrefix(of stem: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: #"^\d{4}-\d{2}-\d{2}"#) else { return nil }
        let ns = stem as NSString
        guard let m = re.firstMatch(in: stem, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return ns.substring(with: m.range)
    }
}

private extension String {
    var nonEmpty: String? {
        trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : self
    }
}
