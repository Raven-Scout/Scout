import SwiftUI

/// Read-mode rendering of a knowledge-base markdown document. Splits the source
/// line-by-line into structural blocks (headings, lists, tables, blockquotes,
/// code fences, rules, prose) and renders each with the editorial design system.
/// Inline formatting and `[[wikilinks]]` are delegated to `InlineMarkdownText`.
///
/// For business readability the view (a) constrains prose to a comfortable
/// reading column, (b) renders GitHub-style tables as a real grid, and (c)
/// collapses the noisy leading metadata (`**Last updated:** / **Prev:** /
/// **Parent:**` changelog + YAML frontmatter) into a closed disclosure so the
/// substance sits at the top.
struct KBMarkdownPreview: View {
    let source: String

    /// Comfortable reading measure for prose; tables/code may exceed it (they
    /// scroll horizontally inside their own container).
    private let readingWidth: CGFloat = 760

    @State private var showMeta = false

    var body: some View {
        let (frontmatter, body) = Self.splitFrontmatter(source)
        let parts = Self.partition(Self.parse(body))
        let hasMeta = !parts.history.isEmpty || (frontmatter?.isEmpty == false)

        HStack(spacing: 0) {
            Spacer(minLength: 0)
            VStack(alignment: .leading, spacing: 12) {
                if let title = parts.title { render(title) }
                if hasMeta { metadataDisclosure(frontmatter: frontmatter, history: parts.history) }
                ForEach(Array(parts.rest.enumerated()), id: \.offset) { _, block in
                    render(block)
                }
            }
            .frame(maxWidth: readingWidth, alignment: .leading)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Metadata disclosure

    @ViewBuilder
    private func metadataDisclosure(frontmatter: [String]?, history: [Block]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { withAnimation(.easeInOut(duration: 0.15)) { showMeta.toggle() } } label: {
                HStack(spacing: 6) {
                    Image(systemName: showMeta ? "chevron.down" : "chevron.right")
                        .font(.system(size: 9, weight: .semibold)).foregroundStyle(DS.Ink.p4)
                    Text("History & properties")
                        .font(DS.sans(11, weight: .medium)).foregroundStyle(DS.Ink.p3)
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if showMeta {
                VStack(alignment: .leading, spacing: 8) {
                    if let frontmatter, !frontmatter.isEmpty {
                        VStack(alignment: .leading, spacing: 3) {
                            ForEach(Array(frontmatter.enumerated()), id: \.offset) { _, line in
                                Text(line).font(DS.mono(11)).foregroundStyle(DS.Ink.p3)
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                    }
                    ForEach(Array(history.enumerated()), id: \.offset) { _, block in
                        render(block)
                    }
                }
                .padding(.leading, 14)
            }
        }
        .padding(10)
        .neumorphicPressed(cornerRadius: 6)
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
                .padding(.top, level <= 2 ? 8 : 2)

        case .prose(let text):
            InlineMarkdownText(text)
                .font(DS.serif(14))
                .foregroundStyle(DS.Ink.p2)
                .textSelection(.enabled)
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)

        case .listItem(let depth, let ordinal, let text):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(ordinal ?? "•")
                    .font(DS.sans(13))
                    .foregroundStyle(DS.Ink.p3)
                    .frame(minWidth: 14, alignment: .trailing)
                InlineMarkdownText(text)
                    .font(DS.serif(14))
                    .foregroundStyle(DS.Ink.p2)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(depth) * 16)

        case .quote(let text):
            HStack(spacing: 10) {
                Rectangle().fill(DS.Accent.fill.opacity(0.5)).frame(width: 2)
                InlineMarkdownText(text)
                    .font(DS.serif(14))
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

        case .table(let headers, let rows):
            KBTableBlockView(headers: headers, rows: rows)

        case .rule:
            EditorialRule().padding(.vertical, 2)
        }
    }

    private func headingSize(_ level: Int) -> CGFloat {
        switch level {
        case 1: return 23
        case 2: return 18.5
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
        case table(headers: [String], rows: [[String]])
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

    /// Split the parsed blocks into a leading title (first H1), the contiguous
    /// metadata/changelog blocks that follow it, and the rest of the document.
    /// Only metadata appearing before the first real content block is collapsed.
    static func partition(_ blocks: [Block]) -> (title: Block?, history: [Block], rest: [Block]) {
        var title: Block? = nil
        var history: [Block] = []
        var rest: [Block] = []
        var leading = true
        for block in blocks {
            if leading {
                if title == nil, history.isEmpty,
                   case .heading(let lvl, _) = block, lvl == 1 {
                    title = block
                    continue
                }
                if isMetadata(block) { history.append(block); continue }
                leading = false
            }
            rest.append(block)
        }
        return (title, history, rest)
    }

    /// A prose block is "metadata" when it's the file's changelog/parent header
    /// (`**Last updated:**`, `**Prev:**`, `**Parent:**`).
    static func isMetadata(_ block: Block) -> Bool {
        guard case .prose(let text) = block else { return false }
        let t = text.trimmingCharacters(in: .whitespaces)
        return t.hasPrefix("**Last updated:**")
            || t.hasPrefix("**Prev:**")
            || t.hasPrefix("**Parent:**")
    }

    static func parse(_ body: String) -> [Block] {
        var blocks: [Block] = []
        var prose: [String] = []
        var inCode = false
        var code: [String] = []
        let lines = body.components(separatedBy: "\n")

        func flushProse() {
            let joined = prose.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { blocks.append(.prose(joined)) }
            prose.removeAll(keepingCapacity: true)
        }

        var i = 0
        while i < lines.count {
            let line = lines[i]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            // Code fence (takes precedence so `|` inside code isn't a table).
            if trimmed.hasPrefix("```") {
                if inCode {
                    blocks.append(.code(code.joined(separator: "\n")))
                    code.removeAll(keepingCapacity: true)
                    inCode = false
                } else {
                    flushProse()
                    inCode = true
                }
                i += 1; continue
            }
            if inCode { code.append(line); i += 1; continue }

            // GitHub-style table: a row line followed by a `|---|` separator.
            if trimmed.contains("|"), i + 1 < lines.count, isTableSeparator(lines[i + 1]) {
                flushProse()
                let headers = splitRow(line)
                var rows: [[String]] = []
                i += 2  // skip header + separator
                while i < lines.count {
                    let rowTrimmed = lines[i].trimmingCharacters(in: .whitespaces)
                    if rowTrimmed.isEmpty || !rowTrimmed.contains("|") { break }
                    if isTableSeparator(lines[i]) { i += 1; continue }
                    var cells = splitRow(lines[i])
                    if cells.count < headers.count {
                        cells += Array(repeating: "", count: headers.count - cells.count)
                    } else if cells.count > headers.count {
                        cells = Array(cells.prefix(headers.count))
                    }
                    rows.append(cells)
                    i += 1
                }
                blocks.append(.table(headers: headers, rows: rows))
                continue
            }

            if trimmed.isEmpty { flushProse(); i += 1; continue }

            if trimmed == "---" || trimmed == "***" || trimmed == "___" {
                flushProse(); blocks.append(.rule); i += 1; continue
            }

            if let heading = parseHeading(trimmed) {
                flushProse(); blocks.append(heading); i += 1; continue
            }

            if trimmed.hasPrefix(">") {
                flushProse()
                blocks.append(.quote(String(trimmed.dropFirst()).trimmingCharacters(in: .whitespaces)))
                i += 1; continue
            }

            if let item = parseListItem(line) {
                flushProse(); blocks.append(item); i += 1; continue
            }

            prose.append(line); i += 1
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

        if let first = content.first, "-*+".contains(first),
           content.dropFirst().first == " " {
            return .listItem(depth: depth, ordinal: nil,
                             text: content.dropFirst(2).trimmingCharacters(in: .whitespaces))
        }
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

    // MARK: - Table helpers

    /// A `|---|:--:|` row: only pipes, dashes, colons and spaces, with at least
    /// one dash. Distinguishes a table separator from a `---` horizontal rule
    /// (which has no pipe).
    static func isTableSeparator(_ line: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard t.contains("-"), t.contains("|") else { return false }
        return t.allSatisfy { "|-: ".contains($0) }
    }

    /// Split one table row into trimmed cells, honoring `\|` escapes (the KB uses
    /// them inside wikilinks like `[[people\|Alias]]`) and dropping the empty
    /// cells produced by leading/trailing pipes.
    static func splitRow(_ line: String) -> [String] {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        var cells: [String] = []
        var current = ""
        var prevBackslash = false
        for ch in trimmed {
            if ch == "|" {
                if prevBackslash {
                    current.removeLast()   // drop the escaping backslash
                    current.append("|")
                    prevBackslash = false
                } else {
                    cells.append(current); current = ""
                }
                continue
            }
            current.append(ch)
            prevBackslash = (ch == "\\")
        }
        cells.append(current)
        if cells.first?.trimmingCharacters(in: .whitespaces).isEmpty == true { cells.removeFirst() }
        if cells.last?.trimmingCharacters(in: .whitespaces).isEmpty == true { cells.removeLast() }
        return cells.map { $0.trimmingCharacters(in: .whitespaces) }
    }
}

/// A GitHub-style table. Each column gets a fixed width (narrow for IDs, capped
/// for verbose columns which then wrap); rows are `HStack`s whose height follows
/// the tallest cell, so a long cell can't overflow onto neighbouring rows (the
/// failure mode of `Grid` with multiline cells). Scrolls horizontally when the
/// column total exceeds the reading column.
struct KBTableBlockView: View {
    let headers: [String]
    let rows: [[String]]

    private let minColumnWidth: CGFloat = 70
    private let maxColumnWidth: CGFloat = 300
    private let hPad: CGFloat = 10
    private let charWidth: CGFloat = 6.5

    var body: some View {
        let widths = columnWidths()
        ScrollView(.horizontal, showsIndicators: true) {
            VStack(alignment: .leading, spacing: 0) {
                row(headers, widths: widths, header: true, background: DS.Paper.sunk)
                ForEach(Array(rows.enumerated()), id: \.offset) { idx, cells in
                    row(cells, widths: widths, header: false,
                        background: idx.isMultiple(of: 2) ? .clear : DS.Paper.sunk.opacity(0.35))
                }
            }
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
            .clipShape(RoundedRectangle(cornerRadius: 6))
        }
    }

    private func row(_ cells: [String], widths: [CGFloat], header: Bool, background: Color) -> some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(Array(widths.enumerated()), id: \.offset) { i, width in
                cell(i < cells.count ? cells[i] : "", header: header, width: width)
            }
        }
        .background(background)
        .overlay(alignment: .bottom) { Rectangle().fill(DS.Rule.soft).frame(height: 0.5) }
    }

    private func cell(_ text: String, header: Bool, width: CGFloat) -> some View {
        InlineMarkdownText(text)
            .font(header ? DS.sans(12, weight: .semibold) : DS.serif(12.5))
            .foregroundStyle(header ? DS.Ink.p1 : DS.Ink.p2)
            .textSelection(.enabled)
            .multilineTextAlignment(.leading)
            .padding(.horizontal, hPad)
            .padding(.vertical, 6)
            .frame(width: width, alignment: .topLeading)
    }

    /// Per-column width from the longest cell, clamped to [min, max]; verbose
    /// columns hit the cap and wrap. Padding is added on top of the text width.
    private func columnWidths() -> [CGFloat] {
        (0..<headers.count).map { c in
            var maxChars = headers[c].count
            for cells in rows where c < cells.count {
                maxChars = max(maxChars, cells[c].count)
            }
            let textWidth = min(maxColumnWidth, max(minColumnWidth, CGFloat(maxChars) * charWidth))
            return textWidth + hPad * 2
        }
    }
}
