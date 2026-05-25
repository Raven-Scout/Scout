import Foundation

enum ActionItemsParser {
    // Parser entry point + helpers land over the next tasks.
}

extension ActionItemsParser {
    /// Strip markdown tokens from a subject so it matches the Python CLIs'
    /// ``_strip_markdown_tokens`` output byte-for-byte.
    ///
    /// Token order matters — mirror the Python:
    ///   1. ``~~strike~~`` → ``strike``
    ///   2. ``**bold**`` → ``bold``
    ///   3. `` `code` `` → ``code``
    ///   4. ``[[target]]`` / ``[[target|alias]]`` → ``target``
    ///   5. ``[label](url)`` → ``label``
    static func plainSubject(_ raw: String) -> String {
        var s = raw
        s = replaceRegex(in: s, pattern: #"~~(.+?)~~"#, template: "$1")
        s = replaceRegex(in: s, pattern: #"\*\*(.+?)\*\*"#, template: "$1")
        s = replaceRegex(in: s, pattern: #"`([^`]+)`"#, template: "$1")
        s = replaceRegex(in: s, pattern: #"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]"#, template: "$1")
        s = replaceRegex(in: s, pattern: #"\[([^\]]+)\]\([^)]+\)"#, template: "$1")
        return s
    }

    private static func replaceRegex(in s: String, pattern: String, template: String) -> String {
        guard let re = try? NSRegularExpression(pattern: pattern) else { return s }
        let range = NSRange(s.startIndex..., in: s)
        return re.stringByReplacingMatches(in: s, range: range, withTemplate: template)
    }

    /// Extract a leading `[#XXXX] ` short-prefix marker from a task body and
    /// return both the bare prefix and the body with the marker removed.
    /// Mirrors scout-plugin's `scout.ids.short_prefix_pattern` — Crockford
    /// alphabet, 4 chars, `[#XXXX]` shape. Returns `(nil, raw)` on absence.
    static func extractShortPrefix(_ raw: String) -> (prefix: String?, rest: String) {
        // Crockford alphabet: 0-9 + uppercase A-Z minus I, L, O, U.
        // Pattern allows optional surrounding whitespace so `[#ABCD] **subj**`
        // and `[#ABCD]**subj**` both parse cleanly.
        guard let re = try? NSRegularExpression(
            pattern: #"^\[#([0-9A-HJKMNP-TV-Z]{4})\]\s*"#
        ) else { return (nil, raw) }
        let range = NSRange(raw.startIndex..., in: raw)
        guard let m = re.firstMatch(in: raw, range: range),
              let prefixRange = Range(m.range(at: 1), in: raw),
              let fullRange = Range(m.range, in: raw) else {
            return (nil, raw)
        }
        return (String(raw[prefixRange]), String(raw[fullRange.upperBound...]))
    }
}

extension ActionItemsParser {
    /// Scan ``text`` for Linear IDs, GitHub PR URLs, and Slack thread URLs.
    /// Emits them in first-match order with duplicates removed.
    ///
    /// Regexes mirror ``action-items/render.py``:
    ///   - Linear: ``\b[A-Z]{2,10}-\d+\b`` (any Linear team prefix)
    ///   - GitHub PR: ``https://github\.com/([\w.\-]+)/([\w.\-]+)/pull/(\d+)``
    ///   - Slack: ``https://[\w.\-]+\.slack\.com/archives/[A-Z0-9]+/p\d+(?:\?[^\s)"']+)?``
    static func detectDeepLinks(in text: String) -> [TaskDeepLink] {
        struct Hit { let range: Range<String.Index>; let link: TaskDeepLink }
        var hits: [Hit] = []

        func scan(_ pattern: String, _ make: (NSTextCheckingResult, String) -> TaskDeepLink?) {
            guard let re = try? NSRegularExpression(pattern: pattern) else { return }
            let full = NSRange(text.startIndex..., in: text)
            re.enumerateMatches(in: text, range: full) { match, _, _ in
                guard let match, let range = Range(match.range, in: text) else { return }
                if let link = make(match, text) {
                    hits.append(Hit(range: range, link: link))
                }
            }
        }

        scan(#"\b[A-Z]{2,10}-\d+\b"#) { m, t in
            guard let r = Range(m.range, in: t) else { return nil }
            return .linear(id: String(t[r]))
        }
        scan(#"https://github\.com/([\w.\-]+)/([\w.\-]+)/pull/(\d+)"#) { m, t in
            guard let full = Range(m.range, in: t),
                  let r1 = Range(m.range(at: 1), in: t),
                  let r2 = Range(m.range(at: 2), in: t),
                  let r3 = Range(m.range(at: 3), in: t),
                  let n = Int(t[r3]),
                  let url = URL(string: String(t[full])) else { return nil }
            return .githubPR(repo: "\(t[r1])/\(t[r2])", number: n, rawURL: url)
        }
        scan(#"https://[\w.\-]+\.slack\.com/archives/[A-Z0-9]+/p\d+(?:\?[^\s)\"']+)?"#) { m, t in
            guard let r = Range(m.range, in: t), let url = URL(string: String(t[r])) else { return nil }
            return .slackThread(url)
        }

        hits.sort { $0.range.lowerBound < $1.range.lowerBound }

        var seen: Set<String> = []
        var result: [TaskDeepLink] = []
        for h in hits where seen.insert(h.link.id).inserted {
            result.append(h.link)
        }
        return result
    }
}

extension ActionItemsParser {
    enum ParseError: Error {
        case noTitle
        case invalidDateInFilename
    }

    static func parse(text: String, sourceURL: URL, sourceBytes: Int) throws -> ActionItemsDocument {
        let lines = text.components(separatedBy: "\n")

        // --- title + preamble ---
        var title = ""
        var preamble: [String] = []
        var i = 0
        while i < lines.count {
            let l = lines[i]
            if l.hasPrefix("# ") && title.isEmpty {
                title = String(l.dropFirst(2)).trimmingCharacters(in: .whitespaces)
                i += 1
                break
            }
            i += 1
        }
        // Collect preamble paragraphs until the first H2.
        while i < lines.count {
            let l = lines[i]
            if l.hasPrefix("## ") { break }
            let trimmed = l.trimmingCharacters(in: .whitespaces)
            if trimmed == "---" || trimmed.isEmpty { i += 1; continue }
            preamble.append(trimmed)
            i += 1
        }

        // --- date from filename ---
        let stem = sourceURL.deletingPathExtension().lastPathComponent  // "action-items-2026-04-20"
        let dateString = stem.replacingOccurrences(of: "action-items-", with: "")
        let isoFmt = DateFormatter()
        isoFmt.calendar = Calendar(identifier: .iso8601)
        isoFmt.dateFormat = "yyyy-MM-dd"
        isoFmt.timeZone = TimeZone(identifier: "America/New_York")
        guard let date = isoFmt.date(from: dateString) else {
            throw ParseError.invalidDateInFilename
        }

        // --- sections ---
        var sections: [ActionSection] = []
        var currentTasks: [ActionTask] = []
        var currentBullets: [String] = []
        var currentTables: [ActionSection.Table] = []
        var currentSubheads: [String] = []
        var currentEmoji = ""
        var currentTitle = ""
        var currentKind: ActionSection.Kind = .neutral
        var inSection = false

        var pendingTableHeaders: [String]? = nil
        var pendingTableRows: [[String]] = []

        func flushTable() {
            if let headers = pendingTableHeaders {
                currentTables.append(.init(headers: headers, rows: pendingTableRows))
            }
            pendingTableHeaders = nil
            pendingTableRows = []
        }

        func flushSection() {
            flushTable()
            if inSection {
                sections.append(ActionSection(
                    id: UUID(),
                    emoji: currentEmoji,
                    title: currentTitle,
                    kind: currentKind,
                    tasks: currentTasks,
                    bullets: currentBullets,
                    tables: currentTables,
                    subheads: currentSubheads
                ))
            }
            currentTasks = []
            currentBullets = []
            currentTables = []
            currentSubheads = []
            currentEmoji = ""
            currentTitle = ""
            currentKind = .neutral
        }

        let taskRe = try NSRegularExpression(pattern: #"^(\s*)- \[([ xX])\] (.+?)\s*$"#)
        let commentRe = try NSRegularExpression(pattern: #"^(\s+)>\s+([A-Za-z][A-Za-z0-9._-]*)(?:\s+\(([^)]+)\))?\s*:\s*(.+?)\s*$"#)
        /// Sub-bullet comment shape written by `scoutctl action-items
        /// add-comment` since v0.4: `  - <author>: <text>` (indented dash
        /// rather than blockquote). Author prefix is optional — bare
        /// `  - <text>` falls through to the bullet path and is treated as
        /// task body, not a comment. v0.5.2 added this so comments written
        /// through scoutctl actually round-trip into the app's reparse.
        let subBulletCommentRe = try NSRegularExpression(pattern: #"^(\s+)-\s+([A-Za-z][A-Za-z0-9._-]*)\s*:\s*(.+?)\s*$"#)
        /// scoutctl's snooze marker: `  - snoozed-until: YYYY-MM-DD`,
        /// optionally followed by `(from-kind: <kind>)`. Captured as task
        /// metadata (`task.snoozedUntil`, `task.snoozedFromKind`) rather than
        /// a user comment — without this carve-out it'd render as a comment
        /// from author "snoozed-until", which is just noise.
        let snoozeSubBulletRe = try NSRegularExpression(
            pattern: #"^\s+-\s+snoozed-until:\s*(\d{4}-\d{2}-\d{2})(?:\s*\(from-kind:\s*([A-Za-z]+)\))?\s*$"#
        )
        /// `_(carried in from YYYY-MM-DD)_` annotation extended with an
        /// optional `, was <kind>` tail. A future consolidation pass can
        /// emit the tail so the target-day's renderer recovers the source
        /// section's priority on a carried-in task. The base regex remains
        /// permissive so today's bare annotations continue to parse.
        let carryInFromKindRe = try NSRegularExpression(
            pattern: #"_\(carried in from \d{4}-\d{2}-\d{2}(?:[^)]*?,\s*was\s+([A-Za-z]+))?\)_"#
        )
        /// Obsidian inline-comment style: ``  //==<< text >>==//``.
        /// Attaches to the preceding task the same way ``> …`` comments do.
        /// Accepts an optional leading bullet marker (``-``/``*``/``+``) so
        /// Obsidian-style nested list items like ``  * //==<< … >>==//`` are
        /// recognized alongside the plain indented form.
        let inlineCommentRe = try NSRegularExpression(pattern: #"^(\s+)(?:[-*+]\s+)?//==<<\s*(.+?)\s*>>==//\s*$"#)
        let bulletRe = try NSRegularExpression(pattern: #"^\s*-\s+(.+?)\s*$"#)
        let sectionRe = try NSRegularExpression(pattern: #"^## (\S+?)\s+(.+?)\s*$"#)
        let snoozeSuffixRe = try NSRegularExpression(pattern: #"\s*(?:—|–|-)\s*🛌 Snoozed until (\d{4}-\d{2}-\d{2})$"#)
        let carryInRe = try NSRegularExpression(pattern: #"_\(carried in from (\d{4}-\d{2}-\d{2})\)_"#)
        let snoozeDateFmt = DateFormatter(); snoozeDateFmt.dateFormat = "yyyy-MM-dd"; snoozeDateFmt.timeZone = TimeZone(identifier: "America/New_York")

        while i < lines.count {
            let line = lines[i]
            let stripped = line.trimmingCharacters(in: .whitespaces)

            if stripped == "---" || stripped == "***" {
                flushTable()
                i += 1; continue
            }

            // Section header
            if line.hasPrefix("## ") {
                flushSection()
                let nsLine = line as NSString
                let range = NSRange(location: 0, length: nsLine.length)
                if let m = sectionRe.firstMatch(in: line, range: range) {
                    let emoji = nsLine.substring(with: m.range(at: 1))
                    let rest  = nsLine.substring(with: m.range(at: 2))
                    let trimmedRest = rest.replacingOccurrences(of: #"\s*\(.*?\)\s*$"#, with: "", options: .regularExpression)
                    if isRecognizedEmoji(emoji) {
                        currentEmoji = emoji
                        currentTitle = trimmedRest
                    } else {
                        currentEmoji = ""
                        currentTitle = String(line.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                    }
                } else {
                    currentEmoji = ""
                    currentTitle = String(line.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                }
                currentKind = kindFor(emoji: currentEmoji, title: currentTitle)
                inSection = true
                i += 1; continue
            }

            // Subhead
            if line.hasPrefix("### ") && inSection {
                flushTable()
                currentSubheads.append(String(line.dropFirst(4)).trimmingCharacters(in: .whitespaces))
                i += 1; continue
            }

            // Pipe-table row
            if stripped.hasPrefix("|") && stripped.hasSuffix("|") {
                let inner = String(stripped.dropFirst().dropLast())
                let cells = inner.components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) }
                // separator row?
                let sepRe = try! NSRegularExpression(pattern: #"^:?-+:?$"#)
                let allSep = cells.allSatisfy { c in
                    let r = NSRange(location: 0, length: (c as NSString).length)
                    return sepRe.firstMatch(in: c, range: r) != nil
                }
                if allSep { i += 1; continue }
                if pendingTableHeaders == nil {
                    pendingTableHeaders = cells
                } else {
                    pendingTableRows.append(cells)
                }
                i += 1; continue
            } else {
                flushTable()
            }

            // Task line
            if inSection,
               let m = taskRe.firstMatch(in: line, range: NSRange(location: 0, length: (line as NSString).length)) {
                let nsLine = line as NSString
                let indent = nsLine.substring(with: m.range(at: 1))
                let mark = nsLine.substring(with: m.range(at: 2))
                let rest = nsLine.substring(with: m.range(at: 3))
                let done = mark.lowercased() == "x"
                // Extract `[#XXXX]` short-prefix (4-char Crockford alphabet)
                // if present at the start of the task body, and strip it
                // from the subject so it doesn't pollute the display. Mirrors
                // scout-plugin's parser.py:_PREFIX_REGEX. Allowed chars:
                // 0-9 + uppercase A-Z minus I, L, O, U (Crockford set).
                let (shortPrefix, restWithoutPrefix) = extractShortPrefix(rest)
                let (subject, body) = splitSubjectBody(restWithoutPrefix)
                let plainSubj = plainSubject(subject)
                let deepLinks = detectDeepLinks(in: rest)
                var snoozedUntil: Date? = nil
                if let sm = snoozeSuffixRe.firstMatch(in: body, range: NSRange(location: 0, length: (body as NSString).length)),
                   let r = Range(sm.range(at: 1), in: body) {
                    snoozedUntil = snoozeDateFmt.date(from: String(body[r]))
                }
                var carriedInFrom: Date? = nil
                var carryInKind: ActionSection.Kind? = nil
                if let cm = carryInRe.firstMatch(in: body, range: NSRange(location: 0, length: (body as NSString).length)),
                   let r = Range(cm.range(at: 1), in: body) {
                    carriedInFrom = snoozeDateFmt.date(from: String(body[r]))
                }
                if let cm = carryInFromKindRe.firstMatch(in: body, range: NSRange(location: 0, length: (body as NSString).length)),
                   cm.range(at: 1).location != NSNotFound,
                   let r = Range(cm.range(at: 1), in: body) {
                    carryInKind = ActionSection.Kind(rawValue: String(body[r]).lowercased())
                }
                currentTasks.append(ActionTask(
                    id: UUID(),
                    lineNumber: i + 1,
                    done: done,
                    subject: subject,
                    plainSubject: plainSubj,
                    body: body,
                    comments: [],
                    deepLinks: deepLinks,
                    snoozedUntil: snoozedUntil,
                    carriedInFrom: carriedInFrom,
                    indentLevel: indentLevelFor(indent),
                    shortPrefix: shortPrefix,
                    snoozedFromKind: carryInKind
                ))
                i += 1; continue
            }

            // Comment line (indented quote attached to the last task)
            if inSection,
               let last = currentTasks.last,
               let cm = commentRe.firstMatch(in: line, range: NSRange(location: 0, length: (line as NSString).length)) {
                let nsLine = line as NSString
                let author = nsLine.substring(with: cm.range(at: 2))
                let ts = cm.range(at: 3).location != NSNotFound ? nsLine.substring(with: cm.range(at: 3)) : ""
                let body = nsLine.substring(with: cm.range(at: 4))
                var updated = last
                let newComment = TaskComment(author: author, timestamp: ts, text: body)
                updated = ActionTask(
                    id: last.id,
                    lineNumber: last.lineNumber,
                    done: last.done,
                    subject: last.subject,
                    plainSubject: last.plainSubject,
                    body: last.body,
                    comments: last.comments + [newComment],
                    deepLinks: last.deepLinks,
                    snoozedUntil: last.snoozedUntil,
                    carriedInFrom: last.carriedInFrom,
                    indentLevel: last.indentLevel,
                    shortPrefix: last.shortPrefix,
                    snoozedFromKind: last.snoozedFromKind
                )
                currentTasks[currentTasks.count - 1] = updated
                i += 1; continue
            }

            // Sub-bullet snooze marker: `  - snoozed-until: YYYY-MM-DD
            // [(from-kind: KIND)]`. Promote the date/kind onto the task
            // record and consume the line — falling through to
            // subBulletCommentRe would otherwise expose it as a comment from
            // author "snoozed-until".
            if inSection,
               let last = currentTasks.last,
               let sm = snoozeSubBulletRe.firstMatch(in: line, range: NSRange(location: 0, length: (line as NSString).length)) {
                let nsLine = line as NSString
                let dateStr = nsLine.substring(with: sm.range(at: 1))
                let parsedDate = snoozeDateFmt.date(from: dateStr)
                var parsedKind: ActionSection.Kind? = nil
                if sm.range(at: 2).location != NSNotFound {
                    let kindStr = nsLine.substring(with: sm.range(at: 2)).lowercased()
                    parsedKind = ActionSection.Kind(rawValue: kindStr)
                }
                let updated = ActionTask(
                    id: last.id,
                    lineNumber: last.lineNumber,
                    done: last.done,
                    subject: last.subject,
                    plainSubject: last.plainSubject,
                    body: last.body,
                    comments: last.comments,
                    deepLinks: last.deepLinks,
                    snoozedUntil: parsedDate ?? last.snoozedUntil,
                    carriedInFrom: last.carriedInFrom,
                    indentLevel: last.indentLevel,
                    shortPrefix: last.shortPrefix,
                    snoozedFromKind: parsedKind ?? last.snoozedFromKind
                )
                currentTasks[currentTasks.count - 1] = updated
                i += 1; continue
            }

            // Sub-bullet comment line attached to the last task: scoutctl
            // writes `  - <author>: <text>`. Distinct from the blockquote
            // form above. Match must run BEFORE the bare-bullet `bulletRe`
            // path so `  - jordan: hello` becomes a comment rather than a
            // sub-task body.
            if inSection,
               let last = currentTasks.last,
               let cm = subBulletCommentRe.firstMatch(in: line, range: NSRange(location: 0, length: (line as NSString).length)) {
                let nsLine = line as NSString
                let author = nsLine.substring(with: cm.range(at: 2))
                let body = nsLine.substring(with: cm.range(at: 3))
                let newComment = TaskComment(author: author, timestamp: "", text: body)
                let updated = ActionTask(
                    id: last.id,
                    lineNumber: last.lineNumber,
                    done: last.done,
                    subject: last.subject,
                    plainSubject: last.plainSubject,
                    body: last.body,
                    comments: last.comments + [newComment],
                    deepLinks: last.deepLinks,
                    snoozedUntil: last.snoozedUntil,
                    carriedInFrom: last.carriedInFrom,
                    indentLevel: last.indentLevel,
                    shortPrefix: last.shortPrefix,
                    snoozedFromKind: last.snoozedFromKind
                )
                currentTasks[currentTasks.count - 1] = updated
                i += 1; continue
            }

            // Obsidian inline-comment style: `//==<< text >>==//`
            if inSection,
               let last = currentTasks.last,
               let im = inlineCommentRe.firstMatch(in: line, range: NSRange(location: 0, length: (line as NSString).length)) {
                let nsLine = line as NSString
                let body = nsLine.substring(with: im.range(at: 2))
                let author = UserDefaults.standard.string(forKey: "authorName") ?? "user"
                let newComment = TaskComment(author: author, timestamp: "", text: body)
                let updated = ActionTask(
                    id: last.id,
                    lineNumber: last.lineNumber,
                    done: last.done,
                    subject: last.subject,
                    plainSubject: last.plainSubject,
                    body: last.body,
                    comments: last.comments + [newComment],
                    deepLinks: last.deepLinks,
                    snoozedUntil: last.snoozedUntil,
                    carriedInFrom: last.carriedInFrom,
                    indentLevel: last.indentLevel,
                    shortPrefix: last.shortPrefix,
                    snoozedFromKind: last.snoozedFromKind
                )
                currentTasks[currentTasks.count - 1] = updated
                i += 1; continue
            }

            // Bullet (section-level)
            if inSection,
               let bm = bulletRe.firstMatch(in: line, range: NSRange(location: 0, length: (line as NSString).length)) {
                let rest = (line as NSString).substring(with: bm.range(at: 1))
                currentBullets.append(rest)
                i += 1; continue
            }

            // Paragraph in a section
            if inSection && !stripped.isEmpty {
                currentBullets.append(stripped)
            }
            i += 1
        }
        flushSection()

        return ActionItemsDocument(
            date: date,
            title: title,
            preamble: preamble,
            sections: sections,
            sourceURL: sourceURL,
            sourceBytes: sourceBytes
        )
    }

    // --- helpers ---

    private static let recognizedEmojiPrefixes: Set<String> = ["🔴", "🟡", "🟢", "💡", "📅", "✅", "📋"]

    private static func isRecognizedEmoji(_ s: String) -> Bool {
        if recognizedEmojiPrefixes.contains(s) { return true }
        // Fall back to the Unicode emoji ranges render.py accepts.
        if let first = s.unicodeScalars.first,
           (0x2600...0x27BF).contains(Int(first.value))
           || (0x1F300...0x1FAFF).contains(Int(first.value)) {
            return true
        }
        return false
    }

    /// Translate a leading-whitespace indent prefix (group 1 of ``taskRe``)
    /// into a markdown-list nesting depth. Mirrors how the action-items files
    /// are typeset: 1 tab = 1 level, otherwise 2 spaces = 1 level. Mixed
    /// indentation sums correctly (1 tab + 2 spaces = level 2).
    static func indentLevelFor(_ indent: String) -> Int {
        var tabs = 0
        var spaces = 0
        for ch in indent {
            if ch == "\t" { tabs += 1 }
            else if ch == " " { spaces += 1 }
        }
        return tabs + spaces / 2
    }

    static func kindFor(emoji: String, title: String) -> ActionSection.Kind {
        switch emoji {
        case "🔴": return .urgent
        case "🟡": return .todo
        case "🟢": return .watching
        case "💡": return .focus
        case "📅": return .meetings
        case "✅": return .done
        case "📋": return .digest
        default: break
        }
        if title.lowercased().contains("personal") { return .personal }
        return .neutral
    }

    /// Split a task line into (subject, body) on the first ` — ` / ` – ` / ` - `
    /// that falls outside ``**bold**``, ``~~strike~~``, `` `code` ``, ``[[wiki]]``,
    /// and ``[label](url)`` tokens. Falls back to ``": "`` separator. Mirrors
    /// ``action-items/render.py`` ``_split_subject``.
    static func splitSubjectBody(_ rest: String) -> (String, String) {
        let separators = [" — ", " – ", " - "]
        if let idx = firstSeparatorOutsideTokens(in: rest, separators: separators) {
            for sep in separators {
                let sepLen = sep.count
                if rest.distance(from: idx, to: rest.endIndex) >= sepLen,
                   rest[idx ..< rest.index(idx, offsetBy: sepLen)] == sep {
                    return (
                        String(rest[..<idx]).trimmingCharacters(in: .whitespaces),
                        String(rest[rest.index(idx, offsetBy: sepLen)...]).trimmingCharacters(in: .whitespaces)
                    )
                }
            }
        }
        if let idx = firstSeparatorOutsideTokens(in: rest, separators: [": "]) {
            let sepLen = 2
            return (
                String(rest[..<idx]).trimmingCharacters(in: .whitespaces),
                String(rest[rest.index(idx, offsetBy: sepLen)...]).trimmingCharacters(in: .whitespaces)
            )
        }
        return (rest, "")
    }

    private static func firstSeparatorOutsideTokens(in text: String, separators: [String]) -> String.Index? {
        var inBold = false, inStrike = false, inCode = false
        var bracketDepth = 0, parenDepth = 0
        var i = text.startIndex
        while i < text.endIndex {
            let rem = text[i...]
            let two = rem.prefix(2)
            let ch = text[i]
            if ch == "`" && !inBold && !inStrike { inCode.toggle(); i = text.index(after: i); continue }
            if inCode { i = text.index(after: i); continue }
            if two == "**" { inBold.toggle(); i = text.index(i, offsetBy: 2); continue }
            if two == "~~" { inStrike.toggle(); i = text.index(i, offsetBy: 2); continue }
            if two == "[[" { bracketDepth += 1; i = text.index(i, offsetBy: 2); continue }
            if two == "]]" && bracketDepth > 0 { bracketDepth -= 1; i = text.index(i, offsetBy: 2); continue }
            if ch == "[" && bracketDepth == 0 { bracketDepth = 1; i = text.index(after: i); continue }
            if ch == "]" && bracketDepth > 0 && two != "]]" {
                bracketDepth = 0
                let next = text.index(after: i)
                if next < text.endIndex && text[next] == "(" {
                    parenDepth = 1
                    i = text.index(i, offsetBy: 2); continue
                }
                i = text.index(after: i); continue
            }
            if ch == ")" && parenDepth > 0 { parenDepth -= 1; i = text.index(after: i); continue }
            if !inBold && !inStrike && bracketDepth == 0 && parenDepth == 0 {
                for sep in separators {
                    let sepLen = sep.count
                    if text.distance(from: i, to: text.endIndex) >= sepLen,
                       text[i ..< text.index(i, offsetBy: sepLen)] == sep {
                        return i
                    }
                }
            }
            i = text.index(after: i)
        }
        return nil
    }
}
