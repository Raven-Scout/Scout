import SwiftUI

/// Read-mode rendering of a knowledge-base markdown document. Splits the source
/// line-by-line into structural blocks (headings, lists, blockquotes, code
/// fences, rules, prose) and renders each with the editorial design system.
/// Inline formatting and `[[wikilinks]]` are delegated to `InlineMarkdownText`.
struct KBMarkdownPreview: View {
    let source: String

    var body: some View {
        let (frontmatter, body) = Self.splitFrontmatter(source)
        let blocks = Self.parse(body)
        VStack(alignment: .leading, spacing: 12) {
            if let frontmatter, !frontmatter.isEmpty {
                frontmatterStrip(frontmatter)
            }
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                render(block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Block rendering

    @ViewBuilder
    private func render(_ block: Block) -> some View {
        switch block {
        case .heading(let level, let text):
            InlineMarkdownText(text)
                .font(DS.serif(headingSize(level), weight: level <= 2 ? .semibold : .medium))
                .foregroundStyle(DS.Ink.p1)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, level <= 2 ? 6 : 2)

        case .prose(let text):
            InlineMarkdownText(text)
                .font(DS.serif(13.5))
                .foregroundStyle(DS.Ink.p2)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)

        case .listItem(let depth, let ordinal, let text):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(ordinal ?? "•")
                    .font(DS.sans(13))
                    .foregroundStyle(DS.Ink.p3)
                    .frame(minWidth: 14, alignment: .trailing)
                InlineMarkdownText(text)
                    .font(DS.serif(13.5))
                    .foregroundStyle(DS.Ink.p2)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(depth) * 16)

        case .quote(let text):
            HStack(spacing: 10) {
                Rectangle().fill(DS.Accent.fill.opacity(0.5)).frame(width: 2)
                InlineMarkdownText(text)
                    .font(DS.serif(13.5))
                    .foregroundStyle(DS.Ink.p3)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }

        case .code(let code):
            Text(code)
                .font(DS.mono(12))
                .foregroundStyle(DS.Ink.p1)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .neumorphicPressed(cornerRadius: 6)
                .fixedSize(horizontal: false, vertical: true)

        case .rule:
            EditorialRule().padding(.vertical, 2)
        }
    }

    private func frontmatterStrip(_ lines: [String]) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                Text(line)
                    .font(DS.mono(11))
                    .foregroundStyle(DS.Ink.p3)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(10)
        .neumorphicPressed(cornerRadius: 6)
    }

    private func headingSize(_ level: Int) -> CGFloat {
        switch level {
        case 1: return 22
        case 2: return 18
        case 3: return 16
        default: return 14.5
        }
    }

    // MARK: - Parsing

    enum Block: Equatable {
        case heading(level: Int, text: String)
        case prose(String)
        case listItem(depth: Int, ordinal: String?, text: String)
        case quote(String)
        case code(String)
        case rule
    }

    /// Separate a leading `--- ... ---` YAML frontmatter block (if present) from
    /// the document body. Returns the frontmatter lines (without the fences) and
    /// the remaining body text.
    static func splitFrontmatter(_ text: String) -> (frontmatter: [String]?, body: String) {
        let lines = text.components(separatedBy: "\n")
        guard lines.first?.trimmingCharacters(in: .whitespaces) == "---" else {
            return (nil, text)
        }
        var i = 1
        while i < lines.count {
            if lines[i].trimmingCharacters(in: .whitespaces) == "---" {
                let fm = Array(lines[1..<i]).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
                let body = lines[(i + 1)...].joined(separator: "\n")
                    .trimmingCharacters(in: .newlines)
                return (fm, body)
            }
            i += 1
        }
        return (nil, text)  // unterminated fence — treat as plain body
    }

    static func parse(_ body: String) -> [Block] {
        var blocks: [Block] = []
        var prose: [String] = []
        var inCode = false
        var code: [String] = []

        func flushProse() {
            let joined = prose.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { blocks.append(.prose(joined)) }
            prose.removeAll(keepingCapacity: true)
        }

        for rawLine in body.components(separatedBy: "\n") {
            let line = rawLine
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if trimmed.hasPrefix("```") {
                if inCode {
                    blocks.append(.code(code.joined(separator: "\n")))
                    code.removeAll(keepingCapacity: true)
                    inCode = false
                } else {
                    flushProse()
                    inCode = true
                }
                continue
            }
            if inCode { code.append(line); continue }

            if trimmed.isEmpty { flushProse(); continue }

            // Horizontal rule
            if trimmed == "---" || trimmed == "***" || trimmed == "___" {
                flushProse(); blocks.append(.rule); continue
            }

            // ATX heading
            if let heading = parseHeading(trimmed) {
                flushProse(); blocks.append(heading); continue
            }

            // Blockquote
            if trimmed.hasPrefix(">") {
                flushProse()
                blocks.append(.quote(String(trimmed.dropFirst()).trimmingCharacters(in: .whitespaces)))
                continue
            }

            // List item (ordered or unordered)
            if let item = parseListItem(line) {
                flushProse(); blocks.append(item); continue
            }

            prose.append(line)
        }
        if inCode { blocks.append(.code(code.joined(separator: "\n"))) }
        flushProse()
        return blocks
    }

    static func parseHeading(_ trimmed: String) -> Block? {
        guard trimmed.hasPrefix("#") else { return nil }
        var level = 0
        for ch in trimmed { if ch == "#" { level += 1 } else { break } }
        guard level >= 1, level <= 6 else { return nil }
        let rest = trimmed.dropFirst(level)
        guard rest.first == " " else { return nil }
        return .heading(level: level, text: rest.trimmingCharacters(in: .whitespaces))
    }

    static func parseListItem(_ line: String) -> Block? {
        let leading = line.prefix { $0 == " " || $0 == "\t" }
        let depth = leading.reduce(0) { $0 + ($1 == "\t" ? 1 : 0) } + (leading.filter { $0 == " " }.count / 2)
        let content = line[leading.endIndex...]

        // Unordered: -, *, + followed by a space
        if let first = content.first, "-*+".contains(first),
           content.dropFirst().first == " " {
            return .listItem(depth: depth, ordinal: nil,
                             text: content.dropFirst(2).trimmingCharacters(in: .whitespaces))
        }
        // Ordered: <digits>. followed by a space
        let digits = content.prefix { $0.isNumber }
        if !digits.isEmpty {
            let afterDigits = content[digits.endIndex...]
            if afterDigits.first == ".", afterDigits.dropFirst().first == " " {
                return .listItem(depth: depth, ordinal: "\(digits).",
                                 text: afterDigits.dropFirst(2).trimmingCharacters(in: .whitespaces))
            }
        }
        return nil
    }
}
