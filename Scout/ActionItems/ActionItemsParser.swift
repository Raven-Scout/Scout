import CryptoKit
import Foundation

enum ActionItemsParser {
    // Parser entry point + helpers land over the next tasks.

    /// Deterministic ``UUID`` derived from a stable content key.
    ///
    /// Sections and tasks need identities that survive a reparse, otherwise
    /// every comment/snooze write (which triggers `reparseCurrent()`) mints
    /// fresh random UUIDs, SwiftUI can't diff the list against the previous
    /// render, and the `ScrollView` snaps back to the top mid-edit. Hashing a
    /// content+position key into the 16 bytes of a UUID gives the same row the
    /// same identity across reparses, so SwiftUI updates in place and the
    /// scroll offset holds. MD5 is used purely as a 128-bit content hash here
    /// — there is no security dependency on it.
    static func stableID(_ key: String) -> UUID {
        let digest = Insecure.MD5.hash(data: Data(key.utf8))
        let b = Array(digest)  // MD5 is exactly 16 bytes — one UUID's worth.
        return UUID(uuid: (b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
                           b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]))
    }
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

    /// Split a leading ordered-list marker (`1. `, `12) `) off a 💡 Focus line.
    ///
    /// The focus section is authored as a numbered markdown list wrapped in
    /// narrative prose — a lede paragraph above it and a `⏸️ Verified
    /// negatives` paragraph below. The parser keeps all of it as bullets, so
    /// the renderer needs to tell the two apart: a line with an ordinal is a
    /// ranked focus item and keeps *its own* number, and everything else is
    /// prose that shouldn't be numbered at all. Without this the view counted
    /// rows itself and drew "1  1. …" over lines that already had a number.
    static func focusOrdinal(_ raw: String) -> (number: Int?, text: String) {
        let range = NSRange(raw.startIndex..., in: raw)
        guard let m = focusOrdinalRe.firstMatch(in: raw, range: range),
              let digits = Range(m.range(at: 1), in: raw),
              let full = Range(m.range, in: raw),
              let n = Int(raw[digits]) else {
            return (nil, raw)
        }
        return (n, String(raw[full.upperBound...]))
    }

    /// `1. ` / `12) ` at the start of a line. The trailing space is required so
    /// `1.5x the cost` stays prose.
    private static let focusOrdinalRe = try! NSRegularExpression(pattern: #"^(\d{1,3})[.)]\s+"#)

    private static func replaceRegex(in s: String, pattern: String, template: String) -> String {
        guard let re = try? NSRegularExpression(pattern: pattern) else { return s }
        let range = NSRange(s.startIndex..., in: s)
        return re.stringByReplacingMatches(in: s, range: range, withTemplate: template)
    }

    /// Extract a leading `[#TAG] ` short-prefix marker from a task body and
    /// return both the bare prefix and the body with the marker removed.
    /// Mirrors scout-plugin's widened `scout.ids.short_prefix_pattern`:
    /// 2–8 chars of `[A-Z0-9]` with at least one letter (so `[#MIRO]`,
    /// `[#AI3026]`, `[#RSM]`, `[#5864M]` are recognized). Pure-numeric refs
    /// like `[#555]` are rejected — those are GitHub issue refs rendered by
    /// the GitHubRefLinkifier. Returns `(nil, raw)` on absence.
    static func extractShortPrefix(_ raw: String) -> (prefix: String?, rest: String) {
        // Lookahead bounds total length to 2–8 [A-Z0-9]; capture group 1
        // requires ≥1 letter. Pattern allows optional surrounding whitespace
        // so `[#ABCD] **subj**` and `[#ABCD]**subj**` both parse cleanly.
        guard let re = try? NSRegularExpression(
            pattern: #"^\[#(?=[A-Z0-9]{2,8}\])([A-Z0-9]*[A-Z][A-Z0-9]*)\]\s*"#
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
    ///   - Slack: ``(?:https://)?[\w.\-]+\.slack\.com/archives/[A-Z0-9]+/p\d+(?:\?[^\s)"']+)?``
    ///            (scheme optional — sessions sometimes write bare hosts; normalized to https)
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
        scan(#"(?:https://)?[\w.\-]+\.slack\.com/archives/[A-Z0-9]+/p\d+(?:\?[^\s)\"']+)?"#) { m, t in
            guard let r = Range(m.range, in: t) else { return nil }
            var raw = String(t[r])
            if !raw.hasPrefix("https://") { raw = "https://" + raw }  // normalize scheme-less hosts
            guard let url = URL(string: raw) else { return nil }
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

    /// `owner/repo#N` GitHub shorthand. Compiled once — `parseRefs` runs per
    /// task, and `NSRegularExpression` compilation dominates the cost
    /// (cf. `InlineMarkdownText.wikilinkRe`).
    private static let ghShorthandRe = try! NSRegularExpression(
        pattern: #"^([A-Za-z0-9][\w.\-]*/[A-Za-z0-9][\w.\-]*)#(\d{1,7})$"#
    )

    /// `[[entity]]` / `[[entity|Label]]` wikilink ref.
    private static let wikilinkRe = try! NSRegularExpression(
        pattern: #"^\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]$"#
    )

    /// Parse the ` · `-separated token list from a task's `- Refs:` sub-bullet
    /// into deep links. Each token runs through ``detectDeepLinks`` first
    /// (Linear id / GitHub PR URL / Slack permalink — this also catches a
    /// Linear id wrapped in a `[[PROJ-1234]]` wikilink), then the Refs-only
    /// shapes: `owner/repo#N` GitHub shorthand, `[[entity]]` / `[[entity|Label]]`
    /// wikilink refs, and `#XREF` cross-reference hashtags. Anything
    /// unrecognized is preserved verbatim as a `.plainRef` so nothing is
    /// dropped.
    static func parseRefs(_ tokensString: String) -> [TaskDeepLink] {
        var result: [TaskDeepLink] = []
        for rawToken in tokensString.components(separatedBy: "·") {
            let token = rawToken.trimmingCharacters(in: .whitespaces)
            if token.isEmpty { continue }

            // 1. Linear id / GitHub PR URL / Slack permalink.
            let detected = detectDeepLinks(in: token)
            if !detected.isEmpty { result.append(contentsOf: detected); continue }

            let ns = token as NSString
            let full = NSRange(location: 0, length: ns.length)

            // 2. GitHub shorthand `owner/repo#N` (no URL form). The canonical
            //    `/issues/N` path redirects to `/pull/N`, matching
            //    GitHubRefLinkifier.
            if let m = ghShorthandRe.firstMatch(in: token, range: full),
               let n = Int(ns.substring(with: m.range(at: 2))),
               let url = URL(string: "https://github.com/\(ns.substring(with: m.range(at: 1)))/issues/\(n)") {
                result.append(.githubPR(repo: ns.substring(with: m.range(at: 1)), number: n, rawURL: url))
                continue
            }

            // 3. Entity wikilink `[[path]]` / `[[path|Label]]`.
            if let m = wikilinkRe.firstMatch(in: token, range: full) {
                let path = ns.substring(with: m.range(at: 1))
                let label = m.range(at: 2).location == NSNotFound ? nil : ns.substring(with: m.range(at: 2))
                result.append(.entity(path: path, label: label))
                continue
            }

            // 4. Cross-reference hashtag `#XREF`. Deferred to `KBTag` so the
            //    Refs block honors the one tag grammar the rest of the app
            //    uses (2–8 `[A-Z0-9]`, at least one letter): digit-leading
            //    mnemonics like `#5864M` are tags, while a purely numeric
            //    `#123` has no letter and stays a GitHub ref.
            if token.hasPrefix("#"), let tag = KBTag.normalized(token) {
                result.append(.crossRef(tag: tag))
                continue
            }

            // 5. Unrecognized — preserve verbatim.
            result.append(.plainRef(text: token))
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
        // Local timezone, matching the engine's date.today() file naming (#46).
        guard let date = ActionItemsDay.date(fromStem: dateString) else {
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

        // --- `<details>` archive regions ---
        // While a region is open the task/bullet accumulators are redirected
        // into it, so the whole existing parse path keeps working unchanged and
        // archived content simply lands somewhere else. See
        // `ActionSection.CollapsedGroup` for why it's kept rather than dropped.
        var currentCollapsed: [ActionSection.CollapsedGroup] = []
        var inCollapsed = false
        var detailsDepth = 0
        var collapsedSummary = ""
        var parkedTasks: [ActionTask] = []
        var parkedBullets: [String] = []
        var parkedTables: [ActionSection.Table] = []
        /// Namespace for task ``stableID`` keys. Without it a parked row and a
        /// live row with the same subject at the same index hash to the same
        /// UUID, and SwiftUI sees two rows claiming one identity.
        var idScope = "0"

        func flushTable() {
            if let headers = pendingTableHeaders {
                currentTables.append(.init(headers: headers, rows: pendingTableRows))
            }
            pendingTableHeaders = nil
            pendingTableRows = []
        }

        /// Seal the open `<details>` region and restore the live accumulators.
        /// A no-op outside a region, so it's safe to call defensively wherever
        /// a region must not outlive its context — notably at a `## ` heading,
        /// which is how the file's unbalanced tags are kept from swallowing
        /// everything after the orphan.
        func closeCollapsedGroup() {
            detailsDepth = 0
            guard inCollapsed else { return }
            inCollapsed = false
            flushTable()
            currentCollapsed.append(ActionSection.CollapsedGroup(
                id: stableID("collapsed|\(sections.count)|\(currentCollapsed.count)|\(collapsedSummary)"),
                summary: collapsedSummary,
                tasks: currentTasks,
                bullets: currentBullets,
                tables: currentTables
            ))
            currentTasks = parkedTasks
            currentBullets = parkedBullets
            currentTables = parkedTables
            parkedTasks = []
            parkedBullets = []
            parkedTables = []
            collapsedSummary = ""
            idScope = "\(sections.count)"
        }

        func flushSection() {
            closeCollapsedGroup()
            flushTable()
            if inSection {
                // Stable identity: section index + kind + title. Survives a
                // reparse so SwiftUI diffs in place instead of resetting the
                // scroll position on every write. See `stableID`.
                sections.append(ActionSection(
                    id: stableID("section|\(sections.count)|\(currentKind.rawValue)|\(currentTitle)"),
                    emoji: currentEmoji,
                    title: currentTitle,
                    kind: currentKind,
                    tasks: currentTasks,
                    bullets: currentBullets,
                    tables: currentTables,
                    subheads: currentSubheads,
                    collapsed: currentCollapsed
                ))
            }
            currentTasks = []
            currentBullets = []
            currentTables = []
            currentSubheads = []
            currentCollapsed = []
            currentEmoji = ""
            currentTitle = ""
            currentKind = .neutral
            idScope = "\(sections.count)"
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
        /// The `- Refs:` sub-bullet (Action Items redesign): an indented
        /// ` · `-separated list of machine refs under a task line. Recognized
        /// like the comment sub-lines and attached to the preceding task as
        /// deep links, never rendered as body prose or a comment. Anchored on
        /// the literal `Refs:` so ordinary `- Source:` / `- Context:` bullets
        /// are untouched. MUST be checked before `subBulletCommentRe`, which
        /// would otherwise expose it as a comment from author "Refs".
        let refsSubBulletRe = try NSRegularExpression(pattern: #"^\s+-\s+Refs:\s*(.+?)\s*$"#)
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
        let snoozeDateFmt = DateFormatter(); snoozeDateFmt.dateFormat = "yyyy-MM-dd"; snoozeDateFmt.timeZone = .current

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

            // `<details>` region boundary. Tag lines are markup, never content
            // — emitting them is what put rows reading `</details>` in Today's
            // Focus. Content *between* the tags falls through to the ordinary
            // branches below and lands in the redirected accumulators, so
            // archived tasks stay real `ActionTask`s inside their group.
            if inSection {
                let scan = HTMLDetailsScanner.scan(line)
                if scan.isTagLine {
                    if detailsDepth == 0 && scan.opens > 0 {
                        // Outermost open: park the live lists and start a group.
                        flushTable()
                        inCollapsed = true
                        collapsedSummary = scan.summary ?? ""
                        parkedTasks = currentTasks
                        parkedBullets = currentBullets
                        parkedTables = currentTables
                        currentTasks = []
                        currentBullets = []
                        currentTables = []
                        idScope = "\(sections.count)|collapsed\(currentCollapsed.count)"
                    } else if let inner = scan.summary, !inner.isEmpty {
                        // Nested open: flattened into the outermost group, but
                        // its summary survives as prose so the archive still
                        // reads in order.
                        currentBullets.append(inner)
                    }
                    detailsDepth += scan.opens
                    detailsDepth -= scan.closes
                    if detailsDepth <= 0 { closeCollapsedGroup() }
                    i += 1; continue
                }
            }

            // Subhead
            if line.hasPrefix("### ") && inSection {
                flushTable()
                // Inside a region this is archived structure, not a live
                // subhead — keep the text with the group rather than promoting
                // it to the section.
                if detailsDepth > 0 {
                    currentBullets.append(String(line.dropFirst(4)).trimmingCharacters(in: .whitespaces))
                } else {
                    currentSubheads.append(String(line.dropFirst(4)).trimmingCharacters(in: .whitespaces))
                }
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
                // Extract the leading `[#TAG]` short-prefix if present at the
                // start of the task body, and strip it from the subject so it
                // doesn't pollute the display. Mirrors scout-plugin's
                // parser.py / scout.ids recognition grammar: 2-8 chars of
                // [A-Z0-9] with >=1 letter (pure-numeric like `[#555]` is a
                // GitHub ref, not a tag). See scout-plugin#117.
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
                // Stable identity: owning scope (the section index, or the
                // collapsed group within it) + task index within that scope +
                // subject. Adding a comment to a task doesn't change any of
                // these, so the row keeps its identity across the reparse the
                // write triggers and the scroll holds. See `stableID`.
                currentTasks.append(ActionTask(
                    id: stableID("task|\(idScope)|\(currentTasks.count)|\(subject)"),
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

            // Refs sub-bullet: `  - Refs: [[people/alex]] · PROJ-1 · … · #XREF`.
            // Parse the ` · `-separated tokens into deep links and merge them
            // onto the preceding task, then consume the line so it never
            // renders as body prose or a comment. Must precede
            // subBulletCommentRe, which would otherwise read it as a comment
            // from author "Refs".
            if inSection,
               let last = currentTasks.last,
               let rm = refsSubBulletRe.firstMatch(in: line, range: NSRange(location: 0, length: (line as NSString).length)) {
                let tokensString = (line as NSString).substring(with: rm.range(at: 1))
                var merged = last.deepLinks
                var seen = Set(merged.map(\.id))
                for link in parseRefs(tokensString) where seen.insert(link.id).inserted {
                    merged.append(link)
                }
                let updated = ActionTask(
                    id: last.id,
                    lineNumber: last.lineNumber,
                    done: last.done,
                    subject: last.subject,
                    plainSubject: last.plainSubject,
                    body: last.body,
                    comments: last.comments,
                    deepLinks: merged,
                    snoozedUntil: last.snoozedUntil,
                    carriedInFrom: last.carriedInFrom,
                    indentLevel: last.indentLevel,
                    shortPrefix: last.shortPrefix,
                    snoozedFromKind: last.snoozedFromKind
                )
                currentTasks[currentTasks.count - 1] = updated
                i += 1; continue
            }

            // Sub-bullet comment line attached to the last task: scoutctl
            // writes `  - <author>: <text>`. Distinct from the blockquote
            // form above. Match must run BEFORE the bare-bullet `bulletRe`
            // path so `  - alex: hello` becomes a comment rather than a
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
