import Foundation

/// The pure half of the web reader (`KBReaderWebView`): what the page is given
/// to render, and every change the page can ask for, applied to the note's
/// markdown source. Each change rewrites only the lines it names, so the rest
/// of the note — and the plugin's structured tokens — stays byte for byte.
///
/// Line numbers are 0-based indices into `source.components(separatedBy: "\n")`,
/// the same numbering `KBDocSegment` and markdown-it's `token.map` use.
nonisolated enum KBReaderDocument {

    // MARK: - What the page renders

    /// `source` with in-app link syntax rewritten to markdown links the page
    /// knows how to route: `[[target|alias]]` → `[alias](scout-wiki://target)`,
    /// `[#TAG]` → a `scout-tag://` chip, `owner/repo#123` → a GitHub link.
    /// Rewrites stay on their own line (no line is added or removed), so the
    /// page's source line numbers are the note's. Frontmatter and fenced code
    /// are left exactly as written.
    @MainActor static func displayMarkdown(_ source: String, linkify: (String) -> String) -> String {
        var lines = source.components(separatedBy: "\n")
        var inFrontmatter = lines.first?.trimmingCharacters(in: .whitespaces) == "---"
        var fence: String? = nil
        for i in lines.indices {
            let t = lines[i].trimmingCharacters(in: .whitespaces)
            if inFrontmatter {
                if i > 0 && t == "---" { inFrontmatter = false }
                continue
            }
            if let open = fence {
                if t.hasPrefix(open) { fence = nil }
                continue
            }
            if t.hasPrefix("```") || t.hasPrefix("~~~") {
                fence = String(t.prefix(3))
                continue
            }
            guard !t.isEmpty else { continue }
            let rewritten = linkify(lines[i])
            // A linkifier must never split a line: that would shift every
            // line number after it. Keep the original if one did.
            lines[i] = rewritten.contains("\n") ? lines[i] : rewritten
        }
        return lines.joined(separator: "\n")
    }

    /// Wikilink targets in `source` that resolve to no note, so the page can
    /// show them as missing rather than as links that go nowhere.
    /// Links inside fenced code are code, not links, so they don't count; nor
    /// do ticket ids (`[[PROJ-1234]]`), which open in Linear, not as notes.
    static func missingTargets(in source: String, resolve: (String) -> String?) -> [String] {
        KnowledgeBaseService.extractWikilinks(withoutFencedCode(source))
            .filter { !KBIndex.isTicketID($0) && resolve($0) == nil }
    }

    /// `source` with every fenced code block's lines (fences included) emptied.
    static func withoutFencedCode(_ source: String) -> String {
        var lines = source.components(separatedBy: "\n")
        var fence: String? = nil
        for i in lines.indices {
            let t = lines[i].trimmingCharacters(in: .whitespaces)
            if let open = fence {
                if t.hasPrefix(open) { fence = nil }
                lines[i] = ""
            } else if t.hasPrefix("```") || t.hasPrefix("~~~") {
                fence = String(t.prefix(3))
                lines[i] = ""
            }
        }
        return lines.joined(separator: "\n")
    }

    // MARK: - Changes the page asks for

    /// The text of lines `start...end`, or nil when the range is out of bounds.
    static func lines(_ source: String, _ start: Int, _ end: Int) -> String? {
        let all = source.components(separatedBy: "\n")
        guard start >= 0, end < all.count, start <= end else { return nil }
        return all[start...end].joined(separator: "\n")
    }

    /// Flip a task-list checkbox on `line`: `- [ ]` ↔ `- [x]`. Nil when the
    /// line isn't a task item.
    static func toggleTask(in source: String, line: Int) -> String? {
        var all = source.components(separatedBy: "\n")
        guard line >= 0, line < all.count else { return nil }
        let text = all[line]
        guard let re = try? NSRegularExpression(pattern: #"^(\s*(?:[-*+]|\d+[.)])\s+\[)([ xX])(\])"#),
              let m = re.firstMatch(in: text, range: NSRange(location: 0, length: (text as NSString).length))
        else { return nil }
        let mark = (text as NSString).substring(with: m.range(at: 2))
        all[line] = (text as NSString).replacingCharacters(in: m.range(at: 2), with: mark == " " ? "x" : " ")
        return all.joined(separator: "\n")
    }

    /// Insert a comment for Scout about `quote`, on a new line after
    /// `afterLine`. The marker keeps the structure it lands in: inside a list
    /// item it is indented to the item's text, inside a blockquote it keeps
    /// the `>` prefix, so neither the list nor the quote is split in two.
    static func insertComment(in source: String, afterLine: Int, quote: String?, comment: String) -> String? {
        guard let marker = ScoutMarker.format(comment, quote: quote) else { return nil }
        let all = source.components(separatedBy: "\n")
        guard !all.isEmpty else { return nil }
        let at = min(max(afterLine, 0), all.count - 1)
        let prefix = continuationPrefix(for: all[at])
        return KBDocSegment.insertLine(in: source, afterLineEnd: at, line: prefix + marker)
    }

    /// The prefix a new line needs to stay inside the block `line` belongs to.
    static func continuationPrefix(for line: String) -> String {
        let indent = String(line.prefix { $0 == " " || $0 == "\t" })
        let rest = line.dropFirst(indent.count)
        if rest.hasPrefix(">") {
            // Keep every `>` level.
            let quotes = rest.prefix { $0 == ">" || $0 == " " }
            return indent + quotes.trimmingCharacters(in: .whitespaces) + " "
        }
        if let re = try? NSRegularExpression(pattern: #"^(?:[-*+]|\d+[.)])\s+"#),
           let m = re.firstMatch(in: String(rest), range: NSRange(location: 0, length: (String(rest) as NSString).length)) {
            return indent + String(repeating: " ", count: m.range.length)
        }
        return indent
    }

    /// Remove one comment marker, looked for on lines `from...to` (the block
    /// the page showed it in). A line left holding only whitespace or a bare
    /// quote prefix is removed with it. Nil when the marker isn't there.
    static func removeMarker(in source: String, marker: String, from: Int, to: Int) -> String? {
        var all = source.components(separatedBy: "\n")
        guard !all.isEmpty else { return nil }
        let lo = max(0, from), hi = min(all.count - 1, max(from, to))
        guard lo <= hi else { return nil }
        for i in lo...hi {
            guard var r = all[i].range(of: marker) else { continue }
            var line = all[i]
            // Take one separating space with it, so no double space is left
            // between the words either side (indentation is never touched).
            if r.lowerBound > line.startIndex, line[line.index(before: r.lowerBound)] == " ",
               line[line.startIndex..<line.index(before: r.lowerBound)].contains(where: { $0 != " " && $0 != "\t" }) {
                r = line.index(before: r.lowerBound)..<r.upperBound
            }
            line.removeSubrange(r)
            let leftover = line.trimmingCharacters(in: CharacterSet(charactersIn: " \t>"))
            if leftover.isEmpty {
                all.remove(at: i)
            } else {
                all[i] = line
            }
            return all.joined(separator: "\n")
        }
        return nil
    }
}

/// One in-place edit made in the reader (a block or a table cell). Every
/// keystroke is applied to the note's draft, so an unfinished edit survives
/// navigating away, counts in the quit prompt, and Esc never throws it away.
/// `snapshot` is the note when the edit began: the page stops re-rendering
/// while the edit is open, so the block being typed into isn't re-parsed under
/// the cursor, and Discard restores it byte for byte.
nonisolated struct KBInlineEdit: Equatable {
    enum Target: Equatable {
        case block(lineStart: Int, lineEnd: Int)
        case cell(sourceLine: Int, col: Int)
    }
    let target: Target
    let snapshot: String
    let original: String
    var buffer: String

    /// The note with this edit applied. Untouched text gives back the
    /// snapshot exactly, so opening an editor never marks the note dirty.
    var applied: String {
        guard buffer != original else { return snapshot }
        switch target {
        case .block(let start, let end):
            return KBDocSegment.replaceLines(in: snapshot, start: start, end: end, with: buffer)
        case .cell(let line, let col):
            return KBDocSegment.replaceCell(in: snapshot, sourceLine: line, col: col, value: buffer)
        }
    }

    /// Whether `source` is still this edit's own output. Anything else
    /// (Reload, another file, a Scout comment) ends the edit.
    func owns(_ source: String) -> Bool { source == applied }

    /// The inline editor's confirm button only closes the editor (the change
    /// is already in the note's draft); the header's Save writes the file.
    static let commitTitle = "Done"
    static let discardTitle = "Discard"

    /// Sending a comment inserts a marker by line number and saves the note at
    /// once. While an inline edit is open the line numbers come from its
    /// snapshot and the draft holds an unfinished edit, so sending waits.
    static func canSendComment(_ text: String, inlineEditOpen: Bool) -> Bool {
        !inlineEditOpen && !text.trimmingCharacters(in: .whitespaces).isEmpty
    }
}
