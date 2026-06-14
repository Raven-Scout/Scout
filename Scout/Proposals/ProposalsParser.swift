import Foundation

/// Parses `dreaming-proposals.md` into a list of ``Proposal`` values.
///
/// The file has a `# Dreaming Proposals` title, a `## How It Works` section,
/// and a `## Proposals` section under which each proposal is a level-3
/// `### <code> — <title>` heading. The parser collects only the level-3
/// sections; everything above the first `###` (the intro / how-it-works prose)
/// is ignored. Pure function — no I/O — so it is trivially unit-testable.
///
/// `nonisolated` because it is pure logic with no shared state: the app target
/// defaults to `MainActor` isolation, but the parser must be callable from the
/// `ProposalsWriter` actor and from background contexts without isolation hops.
nonisolated enum ProposalsParser {

    /// Parse the full markdown text of the proposals file.
    static func parse(text: String) -> [Proposal] {
        let lines = text.components(separatedBy: "\n")
        var proposals: [Proposal] = []

        var i = 0
        while i < lines.count {
            guard isProposalHeading(lines[i]) else { i += 1; continue }
            let headingLine = lines[i]

            // Collect body lines until the next proposal heading, a level-2
            // heading (`## …`), or EOF.
            var bodyLines: [String] = []
            var j = i + 1
            while j < lines.count {
                let line = lines[j]
                if isProposalHeading(line) || isSectionBoundary(line) { break }
                bodyLines.append(line)
                j += 1
            }

            let (code, title) = splitHeading(headingLine)
            let (statusValue, bodyMarkdown) = extractStatus(from: bodyLines)
            proposals.append(Proposal(
                headingLine: headingLine,
                code: code,
                title: title,
                status: ProposalStatus.parse(statusValue ?? ""),
                bodyMarkdown: bodyMarkdown
            ))
            i = j
        }
        return proposals
    }

    // MARK: - Line classification

    /// A proposal heading is a level-3 ATX heading (`### …`). Level-4+ headings
    /// inside a body (`#### …`) are not section starts.
    static func isProposalHeading(_ line: String) -> Bool {
        line.hasPrefix("### ")
    }

    /// A `## …` (or `# …`) heading ends the proposals stream — proposals never
    /// nest under each other and the file's structural sections are level-2.
    private static func isSectionBoundary(_ line: String) -> Bool {
        (line.hasPrefix("## ") || line.hasPrefix("# ")) && !line.hasPrefix("### ")
    }

    // MARK: - Heading

    /// Split `### P-… — Title` into `(code, title)`. Recognizes both the
    /// em-dash separator (` — `, the convention in real files) and the ASCII
    /// hyphen fallback (` - `). Without a separator the whole heading is the
    /// title and the code is empty.
    static func splitHeading(_ headingLine: String) -> (code: String, title: String) {
        var text = headingLine
        if text.hasPrefix("### ") { text = String(text.dropFirst(4)) }
        text = text.trimmingCharacters(in: .whitespaces)

        for separator in [" — ", " – ", " - "] {
            if let range = text.range(of: separator) {
                let code = String(text[..<range.lowerBound]).trimmingCharacters(in: .whitespaces)
                let title = String(text[range.upperBound...]).trimmingCharacters(in: .whitespaces)
                return (code, title)
            }
        }
        return ("", text)
    }

    // MARK: - Status

    /// Pull the first `**Status:**` line out of the body lines, returning its
    /// value plus the remaining body (status line removed) trimmed of leading/
    /// trailing blank lines.
    static func extractStatus(from bodyLines: [String]) -> (value: String?, body: String) {
        var statusValue: String?
        var remaining: [String] = []
        for line in bodyLines {
            if statusValue == nil, let value = Self.statusValue(in: line) {
                statusValue = value
                continue  // drop the status line from the rendered body
            }
            remaining.append(line)
        }
        let body = remaining
            .joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return (statusValue, body)
    }

    /// If `line` is a `**Status:** <value>` marker, return `<value>`.
    static func statusValue(in line: String) -> String? {
        guard let re = try? NSRegularExpression(
            pattern: #"^\s*\*\*\s*Status\s*:\s*\*\*\s*(.*)$"#,
            options: [.caseInsensitive]
        ) else { return nil }
        let ns = line as NSString
        guard let m = re.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)),
              m.range(at: 1).location != NSNotFound else { return nil }
        return ns.substring(with: m.range(at: 1)).trimmingCharacters(in: .whitespaces)
    }
}
