import Foundation

/// An action item's context note, `<vault>/action-items/context/<TAG>.md`.
/// The daily file keeps one line per item; this note holds its status,
/// evidence and links, and goes into Full context / Launch Claude. Read only
/// on an explicit action — never during the parse or the first paint.
nonisolated enum TaskContextNote {
    /// The `[#TAG]` recognition grammar: 2–8 of `[A-Z0-9]`, at least one
    /// letter. Checked here too, independent of the parser, so no other
    /// string can become a path.
    static func isValidTag(_ tag: String) -> Bool {
        guard (2...8).contains(tag.unicodeScalars.count) else { return false }
        var hasLetter = false
        for scalar in tag.unicodeScalars {
            switch scalar {
            case "A"..."Z": hasLetter = true
            case "0"..."9": continue
            default: return false
            }
        }
        return hasLetter
    }

    static func url(for tag: String, scoutDirectory: URL) -> URL? {
        guard isValidTag(tag) else { return nil }
        return scoutDirectory
            .appendingPathComponent("action-items", isDirectory: true)
            .appendingPathComponent("context", isDirectory: true)
            .appendingPathComponent("\(tag).md", isDirectory: false)
    }

    /// The note body without frontmatter, trimmed; nil when there's no tag,
    /// no file, it isn't UTF-8, or nothing is left after the frontmatter.
    static func load(tag: String?, scoutDirectory: URL) -> String? {
        guard let tag, let url = url(for: tag, scoutDirectory: scoutDirectory),
              let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .utf8) else { return nil }
        let body = stripFrontmatter(text).trimmingCharacters(in: .whitespacesAndNewlines)
        return body.isEmpty ? nil : body
    }

    static func stripFrontmatter(_ text: String) -> String {
        let lines = text.components(separatedBy: "\n")
        guard lines.first?.trimmingCharacters(in: .whitespaces) == "---",
              let end = lines.indices.dropFirst().first(where: {
                  lines[$0].trimmingCharacters(in: .whitespaces) == "---"
              })
        else { return text }
        return lines[(end + 1)...].joined(separator: "\n")
    }
}
