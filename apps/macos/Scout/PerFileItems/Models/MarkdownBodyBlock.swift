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

    /// Turn indent widths into nesting levels by rank within the list, so a list
    /// indented by 2 spaces, 4 spaces or tabs nests one level per step either way.
    private static func nested(_ tasks: [MarkdownTask]) -> [MarkdownTask] {
        let widths = Array(Set(tasks.map(\.depth))).sorted()
        return tasks.map { t in
            MarkdownTask(checked: t.checked, text: t.text, depth: widths.firstIndex(of: t.depth) ?? 0)
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
