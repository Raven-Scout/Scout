import Foundation

/// One `- [ ]` / `- [x]` line of a task list. `depth` is its nesting level
/// (0 for a top-level task), from its indent.
nonisolated struct MarkdownTask: Equatable, Sendable {
    let checked: Bool
    let text: String
    var depth: Int = 0
}

/// A structural block of a per-file item body: headings, task lists, quotes,
/// prose paragraphs and fenced code. Inline markup (bold, links,
/// `[[wikilinks]]`) stays in the text and is rendered by `InlineMarkdownText`;
/// markup it can't render (math, a stray `**`) is rewritten first by
/// `InlineMarkupCleanup`.
nonisolated enum MarkdownBodyBlock: Equatable, Sendable {
    case prose(String)
    case code(language: String?, code: String)
    /// An ATX heading (`#` … `######`), level 1–6.
    case heading(level: Int, text: String)
    /// Consecutive task-list lines, in order.
    case tasks([MarkdownTask])
    /// Consecutive `>` lines, without their markers; paragraphs inside are
    /// separated by a blank line. A callout (`> [!note] Title`) carries its
    /// kind, lowercased, and its title (the kind, capitalised, when the line
    /// gives none). A plain quote has neither.
    case quote(kind: String?, title: String?, text: String)

    /// `## Status`, `### Done`: up to six `#`, then a space, then the text. An
    /// optional closing run of `#` needs a space before it, so `## C#` keeps its `#`.
    private static let headingRe = try! NSRegularExpression(pattern: #"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$"#)
    /// `- [ ] text`, `* [x] text`, `1. [X] text`, at any indent.
    private static let taskRe = try! NSRegularExpression(
        pattern: #"^([ \t]*)(?:[-*+]|\d+[.)])[ \t]+\[([ xX])\][ \t]+(\S.*)$"#
    )
    /// `> text`, up to three spaces in. Nested markers (`> > text`) are peeled too.
    private static let quoteRe = try! NSRegularExpression(pattern: #"^ {0,3}>"#)
    /// `[!warning]`, `[!note]+ Title`: the first line of a callout.
    private static let calloutRe = try! NSRegularExpression(pattern: #"^\[!([A-Za-z][\w-]*)\][+-]?[ \t]*(.*)$"#)

    static func blocks(from rawBody: String) -> [MarkdownBodyBlock] {
        let lines = rawBody.components(separatedBy: "\n")
        var blocks: [MarkdownBodyBlock] = []
        var proseBuffer: [String] = []
        var codeBuffer: [String] = []
        var taskBuffer: [MarkdownTask] = []
        var quoteBuffer: [String] = []
        var codeLanguage: String?
        var inCode = false

        func flushProse() {
            let joined = proseBuffer.joined(separator: "\n")
            for para in paragraphs(in: joined) { blocks.append(.prose(InlineMarkupCleanup.clean(para))) }
            proseBuffer.removeAll(keepingCapacity: true)
        }
        func flushTasks() {
            if !taskBuffer.isEmpty { blocks.append(.tasks(nested(taskBuffer))) }
            taskBuffer.removeAll(keepingCapacity: true)
        }
        func flushQuote() {
            if !quoteBuffer.isEmpty, let q = quote(from: quoteBuffer) { blocks.append(q) }
            quoteBuffer.removeAll(keepingCapacity: true)
        }

        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") {
                if inCode {
                    blocks.append(.code(language: codeLanguage, code: codeBuffer.joined(separator: "\n")))
                    codeBuffer.removeAll(keepingCapacity: true)
                    codeLanguage = nil
                    inCode = false
                } else {
                    flushQuote()
                    flushTasks()
                    flushProse()
                    let lang = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                    codeLanguage = lang.isEmpty ? nil : lang
                    inCode = true
                }
                continue
            }
            if inCode { codeBuffer.append(line); continue }
            if let inner = quoteContent(of: line) {
                flushTasks()
                flushProse()
                quoteBuffer.append(inner)
                continue
            }
            // A quote ends at the first line without a `>`, blank or not.
            flushQuote()
            if let task = task(in: line) {
                flushProse()
                taskBuffer.append(task)
                continue
            }
            // A blank line inside a task list doesn't end it; anything else does.
            if trimmed.isEmpty && !taskBuffer.isEmpty { continue }
            flushTasks()
            if let heading = heading(in: line) {
                flushProse()
                blocks.append(heading)
                continue
            }
            proseBuffer.append(line)
        }

        if inCode { blocks.append(.code(language: codeLanguage, code: codeBuffer.joined(separator: "\n"))) }
        flushQuote()
        flushTasks()
        flushProse()
        return blocks
    }

    /// The leading blocks of `blocks` holding at most `budget` characters of
    /// text, cutting the last one short when it overflows. `truncated` is true
    /// when anything was left out.
    ///
    /// Clipping a view does not stop SwiftUI laying out what it hides: a card
    /// that clipped a 220 KB body still measured all of it. Cards render this
    /// prefix instead, so their text layout is bounded by the budget, not the
    /// file. (Splitting the body into blocks happens once per file read, in
    /// `PerFileItem.init`; `prefix` itself is one linear pass.)
    static func prefix(_ blocks: [MarkdownBodyBlock], characters budget: Int)
        -> (blocks: [MarkdownBodyBlock], truncated: Bool) {
        var out: [MarkdownBodyBlock] = []
        var remaining = budget
        for block in blocks {
            let size = block.characterCount
            if size <= remaining {
                out.append(block)
                remaining -= size
                continue
            }
            if let cut = block.cut(to: remaining) { out.append(cut) }
            return (out, true)
        }
        return (out, false)
    }

    var characterCount: Int {
        switch self {
        case .prose(let t), .heading(_, let t): return t.count
        case .code(_, let c): return c.count
        case .tasks(let tasks): return tasks.reduce(0) { $0 + $1.text.count }
        case .quote(_, let title, let text): return (title?.count ?? 0) + text.count
        }
    }

    /// This block shortened to at most `limit` characters, or nil if nothing
    /// meaningful fits.
    private func cut(to limit: Int) -> MarkdownBodyBlock? {
        guard limit > 0 else { return nil }
        switch self {
        case .prose(let t): return .prose(String(t.prefix(limit)) + "…")
        case .code(let lang, let c): return .code(language: lang, code: String(c.prefix(limit)))
        case .heading(let level, let t): return .heading(level: level, text: String(t.prefix(limit)))
        case .tasks(let tasks):
            var kept: [MarkdownTask] = []
            var used = 0
            for task in tasks {
                used += task.text.count
                if used > limit { break }
                kept.append(task)
            }
            return kept.isEmpty ? nil : .tasks(kept)
        case .quote(let kind, let title, let text):
            let room = limit - (title?.count ?? 0)
            guard room > 0 else { return nil }
            return .quote(kind: kind, title: title, text: String(text.prefix(room)) + "…")
        }
    }

    private static func heading(in line: String) -> MarkdownBodyBlock? {
        let ns = line as NSString
        guard let m = headingRe.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)) else {
            return nil
        }
        return .heading(level: m.range(at: 1).length,
                        text: InlineMarkupCleanup.clean(ns.substring(with: m.range(at: 2))))
    }

    private static func task(in line: String) -> MarkdownTask? {
        let ns = line as NSString
        guard let m = taskRe.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)) else {
            return nil
        }
        // `depth` holds the indent width (a tab counts 4) until `nested` ranks it.
        let indent = ns.substring(with: m.range(at: 1)).reduce(0) { $0 + ($1 == "\t" ? 4 : 1) }
        let text = InlineMarkupCleanup.clean(ns.substring(with: m.range(at: 3)).trimmingCharacters(in: .whitespaces))
        return MarkdownTask(checked: ns.substring(with: m.range(at: 2)) != " ", text: text, depth: indent)
    }

    /// The text of a `>` line with every leading marker (and the one space
    /// after each) removed, or nil when the line isn't a quote line.
    private static func quoteContent(of line: String) -> String? {
        var rest = Substring(line)
        guard quoteRe.firstMatch(in: line, range: NSRange(location: 0, length: (line as NSString).length)) != nil else {
            return nil
        }
        while true {
            let trimmed = rest.drop(while: { $0 == " " })
            guard trimmed.first == ">" else { break }
            rest = trimmed.dropFirst()
            if rest.first == " " { rest = rest.dropFirst() }
        }
        return String(rest)
    }

    /// One quote block from its lines (markers already removed). A callout's
    /// first line gives its kind and title; blank lines separate paragraphs.
    private static func quote(from lines: [String]) -> MarkdownBodyBlock? {
        var lines = lines
        var kind: String?
        var title: String?
        if let first = lines.first {
            let ns = first as NSString
            if let m = calloutRe.firstMatch(in: first, range: NSRange(location: 0, length: ns.length)) {
                let k = ns.substring(with: m.range(at: 1)).lowercased()
                let t = ns.substring(with: m.range(at: 2)).trimmingCharacters(in: .whitespaces)
                kind = k
                title = InlineMarkupCleanup.clean(t.isEmpty ? k.prefix(1).uppercased() + k.dropFirst() : t)
                lines.removeFirst()
            }
        }
        let text = paragraphs(in: lines.joined(separator: "\n"))
            .map(InlineMarkupCleanup.clean)
            .joined(separator: "\n\n")
        if text.isEmpty && title == nil { return nil }
        return .quote(kind: kind, title: title, text: text)
    }

    /// Turn indent widths into nesting levels: a task is one level under the
    /// nearest earlier task indented less than it, so a list indented by 2
    /// spaces, 4 spaces or tabs nests one level per step, even when branches
    /// of the same list use different widths.
    private static func nested(_ tasks: [MarkdownTask]) -> [MarkdownTask] {
        var open: [Int] = []  // indent widths of the current ancestors
        return tasks.map { t in
            while let last = open.last, last >= t.depth { open.removeLast() }
            let level = open.count
            open.append(t.depth)
            return MarkdownTask(checked: t.checked, text: t.text, depth: level)
        }
    }

    private static func paragraphs(in text: String) -> [String] {
        text.components(separatedBy: "\n")
            .reduce(into: [[String]]()) { acc, line in
                if line.trimmingCharacters(in: .whitespaces).isEmpty {
                    if acc.last?.isEmpty == false { acc.append([]) }
                } else {
                    if acc.isEmpty { acc.append([]) }
                    acc[acc.count - 1].append(line)
                }
            }
            .map { $0.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }
}
