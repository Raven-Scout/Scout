import Foundation

/// Parses a single dreaming-proposal file (one file in `dreaming-proposals/`)
/// into a ``Proposal``.
///
/// A proposal file is YAML frontmatter between `---` fences, followed by the
/// body:
///
/// ```
/// ---
/// date: 2026-06-15
/// title: Research priority-preemption
/// status: Pending (auto-apply after 2026-06-18)
/// target: RESEARCH.md
/// ---
///
/// # 2026-06-15 — Research priority-preemption
/// **Trigger:** …
/// ```
///
/// Pure functions — no I/O — so they are trivially unit-testable. `nonisolated`
/// because the parser must be callable from background contexts and the
/// `MainActor`-isolated document service alike.
nonisolated enum ProposalsParser {

    /// Parse one proposal file's contents into a ``Proposal``, or `nil` if the
    /// text has no `---` frontmatter (i.e. it is not a proposal file — e.g. the
    /// legacy `dreaming-proposals.md` index, README, etc.).
    static func parseFile(contents: String, fileURL: URL) -> Proposal? {
        guard let (frontmatter, body) = splitFrontmatter(contents) else { return nil }
        let fields = parseFrontmatterFields(frontmatter)
        let stem = fileURL.deletingPathExtension().lastPathComponent

        let date = fields["date"]?.nonEmpty ?? datePrefix(of: stem) ?? ""
        let title = fields["title"]?.nonEmpty ?? stem
        let status = ProposalStatus.parse(fields["status"] ?? "")
        let cleanBody = stripLeadingHeading(body).trimmingCharacters(in: .whitespacesAndNewlines)

        return Proposal(
            fileURL: fileURL,
            date: date,
            title: title,
            status: status,
            bodyMarkdown: cleanBody
        )
    }

    // MARK: - Frontmatter

    /// Split `---\n<frontmatter>\n---\n<body>`. Returns `nil` when the text does
    /// not open with a `---` fence or the closing fence is missing.
    static func splitFrontmatter(_ text: String) -> (frontmatter: String, body: String)? {
        let lines = text.components(separatedBy: "\n")
        guard let first = lines.first,
              first.trimmingCharacters(in: .whitespaces) == "---" else { return nil }
        var frontmatter: [String] = []
        var i = 1
        while i < lines.count {
            if lines[i].trimmingCharacters(in: .whitespaces) == "---" {
                let body = i + 1 < lines.count
                    ? lines[(i + 1)...].joined(separator: "\n")
                    : ""
                return (frontmatter.joined(separator: "\n"), body)
            }
            frontmatter.append(lines[i])
            i += 1
        }
        return nil  // no closing fence
    }

    /// Parse simple `key: value` frontmatter lines. Keys are lowercased; the
    /// value keeps everything after the first colon (so a value may itself
    /// contain colons), with surrounding double quotes stripped.
    static func parseFrontmatterFields(_ frontmatter: String) -> [String: String] {
        var out: [String: String] = [:]
        for line in frontmatter.components(separatedBy: "\n") {
            guard let colon = line.firstIndex(of: ":") else { continue }
            let key = line[..<colon].trimmingCharacters(in: .whitespaces).lowercased()
            guard !key.isEmpty else { continue }
            var value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            if value.count >= 2, value.hasPrefix("\""), value.hasSuffix("\"") {
                value = String(value.dropFirst().dropLast())
            }
            out[key] = value
        }
        return out
    }

    // MARK: - Body

    /// Drop a leading `# …` H1 (and any blank lines before it) from the body —
    /// per-file proposals repeat the title as an H1, which the card already
    /// shows in its header. A `## …` subheading is left intact.
    static func stripLeadingHeading(_ body: String) -> String {
        var lines = body.components(separatedBy: "\n")
        while let first = lines.first,
              first.trimmingCharacters(in: .whitespaces).isEmpty {
            lines.removeFirst()
        }
        if let first = lines.first, first.hasPrefix("# "), !first.hasPrefix("## ") {
            lines.removeFirst()
        }
        return lines.joined(separator: "\n")
    }

    /// First `yyyy-MM-dd` at the start of a filename stem (the naming
    /// convention `YYYY-MM-DD-slug`), used when frontmatter omits `date`.
    static func datePrefix(of stem: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: #"^\d{4}-\d{2}-\d{2}"#) else { return nil }
        let ns = stem as NSString
        guard let m = re.firstMatch(in: stem, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return ns.substring(with: m.range)
    }
}

private extension String {
    /// `nil` when the string is empty after trimming, else self — lets
    /// frontmatter fallbacks treat `title:` (blank) the same as a missing key.
    var nonEmpty: String? {
        trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : self
    }
}
