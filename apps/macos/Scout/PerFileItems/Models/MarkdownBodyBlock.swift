import Foundation

/// One `- [ ]` / `- [x]` line of a task list. `depth` is its nesting level
/// (0 for a top-level task), from its indent.
nonisolated struct MarkdownTask: Equatable, Sendable {
    let checked: Bool
    let text: String
    var depth: Int = 0
}

/// A structural block of a per-file item body: headings, task lists, prose
/// paragraphs and fenced code. Inline markup (bold, links, `[[wikilinks]]`)
/// stays in the text and is rendered by `InlineMarkdownText`.
nonisolated enum MarkdownBodyBlock: Equatable, Sendable {
    case prose(String)
    case code(language: String?, code: String)
    /// An ATX heading (`#` … `######`), level 1–6.
    case heading(level: Int, text: String)
    /// Consecutive task-list lines, in order.
    case tasks([MarkdownTask])

    /// `## Status`, `### Done`: up to six `#`, then a space, then the text. An
    /// optional closing run of `#` needs a space before it, so `## C#` keeps its `#`.
    private static let headingRe = try! NSRegularExpression(pattern: #"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$"#)
    /// `- [ ] text`, `* [x] text`, `1. [X] text`, at any indent.
    private static let taskRe = try! NSRegularExpression(
        pattern: #"^([ \t]*)(?:[-*+]|\d+[.)])[ \t]+\[([ xX])\][ \t]+(\S.*)$"#
    )

    static func blocks(from rawBody: String) -> [MarkdownBodyBlock] {
        let lines = rawBody.components(separatedBy: "\n")
        var blocks: [MarkdownBodyBlock] = []
        var proseBuffer: [String] = []
        var codeBuffer: [String] = []
        var taskBuffer: [MarkdownTask] = []
        var codeLanguage: String?
        var inCode = false

        func flushProse() {
            let joined = proseBuffer.joined(separator: "\n")
            for para in paragraphs(in: joined) { blocks.append(.prose(para)) }
            proseBuffer.removeAll(keepingCapacity: true)
        }
        func flushTasks() {
            if !taskBuffer.isEmpty { blocks.append(.tasks(nested(taskBuffer))) }
            taskBuffer.removeAll(keepingCapacity: true)
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
                    flushTasks()
                    flushProse()
                    let lang = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                    codeLanguage = lang.isEmpty ? nil : lang
                    inCode = true
                }
                continue
            }
            if inCode { codeBuffer.append(line); continue }
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
        }
    }

    private static func heading(in line: String) -> MarkdownBodyBlock? {
        let ns = line as NSString
        guard let m = headingRe.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)) else {
            return nil
        }
        return .heading(level: m.range(at: 1).length, text: ns.substring(with: m.range(at: 2)))
    }

    private static func task(in line: String) -> MarkdownTask? {
        let ns = line as NSString
        guard let m = taskRe.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)) else {
            return nil
        }
        // `depth` holds the indent width (a tab counts 4) until `nested` ranks it.
        let indent = ns.substring(with: m.range(at: 1)).reduce(0) { $0 + ($1 == "\t" ? 4 : 1) }
        let text = ns.substring(with: m.range(at: 3)).trimmingCharacters(in: .whitespaces)
        return MarkdownTask(checked: ns.substring(with: m.range(at: 2)) != " ", text: text, depth: indent)
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
