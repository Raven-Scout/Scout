import SwiftUI

/// A rendered markdown document you edit in place: double-click a paragraph,
/// heading, list item, quote, code block or table cell to edit just that piece;
/// single-click a `[[wikilink]]` still navigates. Every edit replaces only that
/// block's exact source range (or one table cell), so the rest of the file —
/// and the plugin's structured tokens — stays byte-for-byte untouched.
struct KBEditableView: View {
    @Binding var source: String

    private let readingWidth: CGFloat = 760
    @State private var editing: Int? = nil        // lineStart of the segment being edited
    @State private var buffer: String = ""
    @State private var showMeta = false
    @State private var cache = SegmentCache()

    /// Memoizes the segment parse: `body` re-evaluates on every editing/
    /// disclosure state change, but the parse only depends on the source text.
    final class SegmentCache {
        private var source: String = ""
        private var segs: [KBDocSegment] = []
        func segments(for source: String) -> [KBDocSegment] {
            if source != self.source {
                self.source = source
                self.segs = KBDocSegment.segments(from: source)
            }
            return segs
        }
    }

    var body: some View {
        let segs = cache.segments(for: source)
        let parts = KBDocSegment.partition(segs)
        ScrollView {
            HStack(spacing: 0) {
                Spacer(minLength: 0)
                VStack(alignment: .leading, spacing: 12) {
                    if let title = parts.title { segmentView(title) }
                    if !parts.history.isEmpty { metadataDisclosure(parts.history) }
                    ForEach(parts.rest) { segmentView($0) }
                }
                .frame(maxWidth: readingWidth, alignment: .leading)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 24).padding(.vertical, 18)
        }
    }

    // MARK: - Segment view (rendered or editing)

    @ViewBuilder
    private func segmentView(_ seg: KBDocSegment) -> some View {
        if editing == seg.id {
            inlineEditor(seg)
        } else if case .table = seg.kind {
            KBEditableTableView(headers: seg.headers, rows: seg.rows, rowLines: seg.rowLines) { line, col, value in
                source = KBDocSegment.replaceCell(in: source, sourceLine: line, col: col, value: value)
            }
        } else {
            rendered(seg)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
                .onTapGesture(count: 2) { startEdit(seg) }
                .help("Double-click to edit")
        }
    }

    private func inlineEditor(_ seg: KBDocSegment) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            TextEditor(text: $buffer)
                .font(DS.mono(12.5)).foregroundStyle(DS.Ink.p1)
                .scrollContentBackground(.hidden)
                .frame(minHeight: editorHeight(seg))
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 6).fill(DS.Paper.sunk))
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Accent.fill.opacity(0.6), lineWidth: 1))
            HStack(spacing: 8) {
                Spacer()
                Button("Cancel") { editing = nil }
                    .buttonStyle(.plain).font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
                    .keyboardShortcut(.cancelAction)
                Button("Save") { commit(seg) }
                    .buttonStyle(.plain).font(DS.sans(12, weight: .semibold)).foregroundStyle(.white)
                    .padding(.horizontal, 12).padding(.vertical, 5)
                    .background(Capsule().fill(DS.Accent.fill))
                    .keyboardShortcut(.return, modifiers: .command)
            }
        }
        .padding(.vertical, 2)
    }

    private func editorHeight(_ seg: KBDocSegment) -> CGFloat {
        let lines = seg.raw.components(separatedBy: "\n").count
        return min(360, max(40, CGFloat(lines) * 20 + 16))
    }

    private func startEdit(_ seg: KBDocSegment) {
        buffer = seg.raw
        editing = seg.id
    }

    private func commit(_ seg: KBDocSegment) {
        source = KBDocSegment.replaceLines(in: source, start: seg.lineStart, end: seg.lineEnd, with: buffer)
        editing = nil
    }

    // MARK: - Rendering

    @ViewBuilder
    private func rendered(_ seg: KBDocSegment) -> some View {
        switch seg.kind {
        case .heading(let level):
            InlineMarkdownText(headingText(seg.raw))
                .font(DS.serif(headingSize(level), weight: level <= 2 ? .semibold : .medium))
                .foregroundStyle(DS.Ink.p1).fixedSize(horizontal: false, vertical: true)
                .padding(.top, level <= 2 ? 8 : 2)
        case .paragraph:
            InlineMarkdownText(seg.raw)
                .font(DS.serif(14)).foregroundStyle(DS.Ink.p2).lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)
        case .list:
            renderedList(seg.raw)
        case .quote:
            HStack(spacing: 10) {
                Rectangle().fill(DS.Accent.fill.opacity(0.5)).frame(width: 2)
                InlineMarkdownText(seg.raw.replacingOccurrences(of: "> ", with: "").replacingOccurrences(of: ">", with: ""))
                    .font(DS.serif(14)).foregroundStyle(DS.Ink.p3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        case .code:
            Text(stripFence(seg.raw))
                .font(DS.mono(12)).foregroundStyle(DS.Ink.p1)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 12).padding(.vertical, 10)
                .neumorphicPressed(cornerRadius: 6).fixedSize(horizontal: false, vertical: true)
        case .rule:
            EditorialRule().padding(.vertical, 2)
        case .frontmatter:
            metaText(seg.raw)
        case .table:
            EmptyView()   // handled in segmentView
        case .scoutComment:
            InlineMarkdownText(ScoutMarker.body(of: seg.raw) ?? seg.raw)
                .font(DS.sans(12)).foregroundStyle(DS.Accent.ink)
        }
    }

    private func renderedList(_ raw: String) -> some View {
        let (ordinal, text, depth) = listParts(raw)
        return HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(ordinal).font(DS.sans(13)).foregroundStyle(DS.Ink.p3).frame(minWidth: 14, alignment: .trailing)
            InlineMarkdownText(text).font(DS.serif(14)).foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.leading, CGFloat(depth) * 16)
    }

    private func metaText(_ raw: String) -> some View {
        Text(raw).font(DS.mono(11)).foregroundStyle(DS.Ink.p3)
            .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
    }

    @ViewBuilder
    private func metadataDisclosure(_ history: [KBDocSegment]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { withAnimation(.easeInOut(duration: 0.15)) { showMeta.toggle() } } label: {
                HStack(spacing: 6) {
                    Image(systemName: showMeta ? "chevron.down" : "chevron.right")
                        .font(.system(size: 9, weight: .semibold)).foregroundStyle(DS.Ink.p4)
                    Text("History & properties").font(DS.sans(11, weight: .medium)).foregroundStyle(DS.Ink.p3)
                    Spacer(minLength: 0)
                }.contentShape(Rectangle())
            }.buttonStyle(.plain)
            if showMeta {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(history) { seg in
                        rendered(seg)
                            .contentShape(Rectangle())
                            .onTapGesture(count: 2) { startEdit(seg) }
                    }
                }.padding(.leading, 14)
            }
        }
        .padding(10).neumorphicPressed(cornerRadius: 6)
    }

    // MARK: - Text helpers

    private func headingText(_ raw: String) -> String {
        let t = raw.trimmingCharacters(in: .whitespaces)
        return KBMarkdownLexer.heading(t)?.text ?? t
    }
    private func headingSize(_ level: Int) -> CGFloat {
        KBMarkdownLexer.headingSize(level)
    }
    private func stripFence(_ raw: String) -> String {
        var lines = raw.components(separatedBy: "\n")
        if lines.first?.trimmingCharacters(in: .whitespaces).hasPrefix("```") == true { lines.removeFirst() }
        if lines.last?.trimmingCharacters(in: .whitespaces).hasPrefix("```") == true { lines.removeLast() }
        return lines.joined(separator: "\n")
    }
    private func listParts(_ raw: String) -> (ordinal: String, text: String, depth: Int) {
        guard let item = KBMarkdownLexer.listItem(raw) else {
            return ("•", raw.trimmingCharacters(in: .whitespaces), 0)
        }
        return (item.ordinal ?? "•", item.text, item.depth)
    }
}

/// A table whose cells you edit by double-clicking. Renders like the read-only
/// table but swaps the double-clicked cell for a text field; committing calls
/// back with the cell's source line + column so only that cell is rewritten.
struct KBEditableTableView: View {
    let headers: [String]
    let rows: [[String]]
    let rowLines: [Int]
    let onEditCell: (_ sourceLine: Int, _ col: Int, _ value: String) -> Void

    @State private var editing: EditKey? = nil
    @State private var buffer = ""

    private struct EditKey: Equatable { let row: Int; let col: Int }

    private let minColumnWidth: CGFloat = 70
    private let maxColumnWidth: CGFloat = 300
    private let hPad: CGFloat = 10
    private let charWidth: CGFloat = 6.5

    var body: some View {
        let widths = columnWidths()
        ScrollView(.horizontal, showsIndicators: true) {
            VStack(alignment: .leading, spacing: 0) {
                headerRow(widths)
                ForEach(Array(rows.enumerated()), id: \.offset) { r, cells in
                    bodyRow(r, cells: cells, widths: widths,
                            background: r.isMultiple(of: 2) ? .clear : DS.Paper.sunk.opacity(0.35))
                }
            }
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
            .clipShape(RoundedRectangle(cornerRadius: 6))
        }
    }

    private func headerRow(_ widths: [CGFloat]) -> some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(Array(widths.enumerated()), id: \.offset) { i, width in
                InlineMarkdownText(i < headers.count ? headers[i] : "")
                    .font(DS.sans(12, weight: .semibold)).foregroundStyle(DS.Ink.p1)
                    .padding(.horizontal, hPad).padding(.vertical, 6)
                    .frame(width: width, alignment: .topLeading)
            }
        }
        .background(DS.Paper.sunk)
        .overlay(alignment: .bottom) { Rectangle().fill(DS.Rule.soft).frame(height: 0.5) }
    }

    private func bodyRow(_ r: Int, cells: [String], widths: [CGFloat], background: Color) -> some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(Array(widths.enumerated()), id: \.offset) { c, width in
                cellView(r, c, value: c < cells.count ? cells[c] : "", width: width)
            }
        }
        .background(background)
        .overlay(alignment: .bottom) { Rectangle().fill(DS.Rule.soft).frame(height: 0.5) }
    }

    @ViewBuilder
    private func cellView(_ r: Int, _ c: Int, value: String, width: CGFloat) -> some View {
        if editing == EditKey(row: r, col: c) {
            TextField("", text: $buffer)
                .textFieldStyle(.plain).font(DS.serif(12.5)).foregroundStyle(DS.Ink.p1)
                .padding(.horizontal, hPad - 2).padding(.vertical, 4)
                .background(RoundedRectangle(cornerRadius: 4).fill(DS.Paper.base))
                .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(DS.Accent.fill.opacity(0.6), lineWidth: 1))
                .frame(width: width, alignment: .topLeading)
                .onSubmit { commit(r, c) }
                .onExitCommand { editing = nil }
        } else {
            InlineMarkdownText(value)
                .font(DS.serif(12.5)).foregroundStyle(DS.Ink.p2)
                .padding(.horizontal, hPad).padding(.vertical, 6)
                .frame(width: width, alignment: .topLeading)
                .contentShape(Rectangle())
                .onTapGesture(count: 2) { buffer = value; editing = EditKey(row: r, col: c) }
                .help("Double-click to edit")
        }
    }

    private func commit(_ r: Int, _ c: Int) {
        if r < rowLines.count { onEditCell(rowLines[r], c, buffer) }
        editing = nil
    }

    private func columnWidths() -> [CGFloat] {
        (0..<headers.count).map { c in
            var maxChars = headers[c].count
            for cells in rows where c < cells.count { maxChars = max(maxChars, cells[c].count) }
            return min(maxColumnWidth, max(minColumnWidth, CGFloat(maxChars) * charWidth)) + hPad * 2
        }
    }
}
