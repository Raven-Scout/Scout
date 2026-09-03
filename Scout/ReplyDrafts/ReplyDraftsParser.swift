import Foundation

/// Parses a single reply-draft file (one file in `drafts/`) into a ``ReplyDraft``.
///
/// A draft file is YAML frontmatter between `---` fences, followed by the
/// drafted reply body:
///
/// ```
/// ---
/// tag: NAHSEND
/// channel: email
/// loop_type: direct-debt
/// to: "Priya <priya@example.com>"
/// thread_ref: "https://mail.google.com/…"
/// subject: "Re: Q3 budget"
/// status: draft
/// created: 2026-06-29
/// context_answer_ref: ""
/// ---
///
/// Hi Priya, …
/// ```
///
/// Pure functions — no I/O — so they are trivially unit-testable. `nonisolated`
/// so the parser is callable from background contexts and the `MainActor`
/// document service alike. Frontmatter helpers mirror `ProposalsParser`
/// (parsing is hand-rolled per feature in this app).
nonisolated enum ReplyDraftsParser {

    /// Parse one draft file's contents into a ``ReplyDraft``, or `nil` if the
    /// text has no `---` frontmatter (e.g. the `drafts/README.md` doc, which has
    /// none and is therefore skipped).
    static func parseFile(contents: String, fileURL: URL) -> ReplyDraft? {
        guard let (frontmatter, body) = splitFrontmatter(contents) else { return nil }
        let fields = parseFrontmatterFields(frontmatter)
        let stem = fileURL.deletingPathExtension().lastPathComponent

        let tag = fields["tag"]?.nonEmpty ?? stem
        let channel = DraftChannel.parse(fields["channel"] ?? "")
        let loopType = fields["loop_type"] ?? ""
        let to = fields["to"] ?? ""
        let cc = fields["cc"]?.nonEmpty
        let threadRef = fields["thread_ref"] ?? ""
        let subject = fields["subject"]?.nonEmpty
        let status = DraftStatus.parse(fields["status"] ?? "")
        let created = fields["date"]?.nonEmpty ?? fields["created"]?.nonEmpty ?? datePrefix(of: stem) ?? ""
        let contextAnswerRef = fields["context_answer_ref"]?.nonEmpty

        // Split the post-frontmatter text into the sendable reply (before the
        // marker) and the context block (after it). The marker keeps the
        // summary + thread out of what Copy/Mark-sent treat as the email.
        let (sendable, context) = splitContext(body)

        return ReplyDraft(
            fileURL: fileURL,
            tag: tag,
            channel: channel,
            loopType: loopType,
            to: to,
            cc: cc,
            threadRef: threadRef,
            subject: subject,
            status: status,
            created: created,
            contextAnswerRef: contextAnswerRef,
            bodyMarkdown: sendable.trimmingCharacters(in: .whitespacesAndNewlines),
            summary: context.flatMap { parseSummary($0)?.nonEmpty },
            relatedMessages: context.map { parseMessages($0) } ?? []
        )
    }

    // MARK: - Context block (summary + thread)

    /// Marker separating the sendable reply from the thread-context block.
    static let contextMarker = "<!-- scout:context -->"

    /// Split post-frontmatter text at ``contextMarker``. Returns (sendable body,
    /// context block or nil if there is no marker).
    static func splitContext(_ body: String) -> (sendable: String, context: String?) {
        guard let r = body.range(of: contextMarker) else { return (body, nil) }
        return (String(body[..<r.lowerBound]), String(body[r.upperBound...]))
    }

    /// Text under a `## Summary` heading, up to the next `## ` heading.
    static func parseSummary(_ context: String) -> String? {
        sectionBody(context, heading: "## Summary")
    }

    /// Matches `- [YYYY-MM-DD] Sender: text`. The sender group is lazy and the
    /// separator requires a following space, so the split lands on the LAST
    /// `": "` that starts the text rather than the first bare colon — otherwise
    /// a sender carrying a time ("Alex 10:30") is truncated to "Alex 10".
    /// Compiled once; `parseMessages` runs for every draft parse.
    private static let messageLine = try? NSRegularExpression(
        pattern: #"^\s*-\s*\[([^\]]*)\]\s*(.+?):\s(.*)$"#
    )

    /// Parse `- [YYYY-MM-DD] Sender: text` lines under a `## Thread` heading.
    static func parseMessages(_ context: String) -> [DraftMessage] {
        guard let section = sectionBody(context, heading: "## Thread") else { return [] }
        guard let re = messageLine else { return [] }
        var out: [DraftMessage] = []
        for (i, line) in section.components(separatedBy: "\n").enumerated() {
            let ns = line as NSString
            guard let m = re.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)) else { continue }
            out.append(DraftMessage(
                date: ns.substring(with: m.range(at: 1)).trimmingCharacters(in: .whitespaces),
                sender: ns.substring(with: m.range(at: 2)).trimmingCharacters(in: .whitespaces),
                text: ns.substring(with: m.range(at: 3)).trimmingCharacters(in: .whitespaces),
                id: "\(i)"
            ))
        }
        return out
    }

    /// Body of a `## <heading>` section, up to the next `## ` heading or end.
    private static func sectionBody(_ text: String, heading: String) -> String? {
        let lines = text.components(separatedBy: "\n")
        guard let start = lines.firstIndex(where: { $0.trimmingCharacters(in: .whitespaces) == heading }) else { return nil }
        var collected: [String] = []
        for line in lines[(start + 1)...] {
            if line.hasPrefix("## ") { break }
            collected.append(line)
        }
        let body = collected.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        return body.isEmpty ? nil : body
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

    /// Compiled once — see ``messageLine``.
    private static let stemDatePrefix = try? NSRegularExpression(pattern: #"^\d{4}-\d{2}-\d{2}"#)

    /// First `yyyy-MM-dd` at the start of a filename stem, used when frontmatter
    /// omits `created`.
    static func datePrefix(of stem: String) -> String? {
        guard let re = stemDatePrefix else { return nil }
        let ns = stem as NSString
        guard let m = re.firstMatch(in: stem, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return ns.substring(with: m.range)
    }
}

private extension String {
    /// `nil` when the string is empty after trimming, else self.
    var nonEmpty: String? {
        trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : self
    }
}
