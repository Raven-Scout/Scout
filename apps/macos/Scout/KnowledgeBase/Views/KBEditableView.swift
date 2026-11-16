import SwiftUI

/// A rendered markdown document you edit in place: double-click a paragraph,
/// heading, list item, quote, code block or table cell to edit just that piece;
/// single-click a `[[wikilink]]` still navigates. Every edit replaces only that
/// block's exact source range (or one table cell), so the rest of the file —
/// and the plugin's structured tokens — stays byte-for-byte untouched.
struct KBEditableView: View {
    @Binding var source: String

    private let readingWidth: CGFloat = 760
    /// The in-place edit in progress, if any. See `InlineEdit`.
    @State private var inline: InlineEdit? = nil
    @State private var showMeta = false
    @State private var cache = SegmentCache()

    /// Persist immediately after a Scout comment is added or retracted (the
    /// parent routes this to its `save()`). Defaults to no-op so ordinary
    /// double-click edits keep their edit → ⌘S flow.
    var onRequestSave: () -> Void = {}

    @State private var commentingOn: Int? = nil   // segment id the composer is open on
    @State private var commentBuffer: String = ""
    @State private var hovering: Int? = nil        // segment id currently hovered

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

    /// One in-place edit in Read mode (a block or a table cell). Every
    /// keystroke is applied to the note's draft (`source`), so an unfinished
    /// inline edit survives navigating away, counts in the quit prompt, and
    /// Esc never throws it away. `snapshot` is the note when the edit began:
    /// the page keeps rendering from it, so the block being typed into isn't
    /// re-parsed under the cursor, and Discard restores it byte for byte.
    struct InlineEdit: Equatable {
        enum Target: Equatable {
            case block(lineStart: Int, lineEnd: Int)
            case cell(segmentID: Int, row: Int, col: Int, sourceLine: Int)
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
            case .cell(_, _, let col, let line):
                return KBDocSegment.replaceCell(in: snapshot, sourceLine: line, col: col, value: buffer)
            }
        }

        /// Whether `source` is still this edit's own output. Anything else
        /// (Reload, another file, a Scout comment) ends the edit.
        func owns(_ source: String) -> Bool { source == applied }
    }

    var body: some View {
        let segs = cache.segments(for: inline?.snapshot ?? source)
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
        .onChange(of: source) { _, new in
            if let edit = inline, !edit.owns(new) { inline = nil }
        }
    }

    // MARK: - Segment view (rendered or editing)

    @ViewBuilder
    private func segmentView(_ seg: KBDocSegment) -> some View {
        if case .scoutComment = seg.kind {
            scoutCommentChip(seg)
        } else if case .block(let start, _) = inline?.target, start == seg.id {
            inlineEditor(seg)
        } else if case .table = seg.kind {
            KBEditableTableView(
                headers: seg.headers, rows: seg.rows,
                editingCell: editingCell(in: seg), buffer: bufferBinding,
                onBeginEdit: { row, col, value in beginCellEdit(seg, row: row, col: col, value: value) },
                onFinishEdit: finishInline)
        } else {
            VStack(alignment: .leading, spacing: 4) {
                rendered(seg)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                    .onTapGesture(count: 2) { startEdit(seg) }
                    .help("Double-click to edit")
                if commentingOn == seg.id {
                    scoutCommentComposer(seg)
                } else if hovering == seg.id && inline == nil {
                    commentAffordance(seg)
                }
            }
            .onKeyWindowHover { hovering = $0 ? seg.id : (hovering == seg.id ? nil : hovering) }
        }
    }

    private func inlineEditor(_ seg: KBDocSegment) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            TextEditor(text: bufferBinding)
                .font(DS.mono(12.5)).foregroundStyle(DS.Ink.p1)
                .scrollContentBackground(.hidden)
                .frame(minHeight: editorHeight(seg))
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 6).fill(DS.Paper.sunk))
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Accent.fill.opacity(0.6), lineWidth: 1))
            HStack(spacing: 8) {
                Spacer()
                Button(Self.inlineDiscardTitle) { discardInline() }
                    .buttonStyle(.plain).font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
                    .help("Put this block back the way it was")
                Button(Self.inlineCommitTitle) { finishInline() }
                    .buttonStyle(.plain).font(DS.sans(12, weight: .semibold)).foregroundStyle(.white)
                    .padding(.horizontal, 12).padding(.vertical, 5)
                    .background(Capsule().fill(DS.Accent.fill))
                    .keyboardShortcut(.return, modifiers: .command)
                    .help("Close the editor. The change is already in the note; Save (⌘S) writes it to disk.")
            }
        }
        .padding(.vertical, 2)
        // Esc closes the editor and keeps the change, like Done. Only the
        // Discard button throws an inline edit away.
        .background {
            Button("", action: finishInline).keyboardShortcut(.cancelAction)
                .opacity(0).accessibilityHidden(true)
        }
    }

    private func editorHeight(_ seg: KBDocSegment) -> CGFloat {
        let lines = seg.raw.components(separatedBy: "\n").count
        return min(360, max(40, CGFloat(lines) * 20 + 16))
    }

    /// The inline editor's confirm button only closes the editor (the change
    /// is already in the note's draft); the header's Save writes the file.
    /// Calling it "Save" made people think the change was on disk.
    static let inlineCommitTitle = "Done"
    static let inlineDiscardTitle = "Discard"

    /// Typing goes into the edit's buffer and straight into the note's draft.
    private var bufferBinding: Binding<String> {
        Binding(
            get: { inline?.buffer ?? "" },
            set: { new in
                guard var edit = inline else { return }
                edit.buffer = new
                inline = edit
                source = edit.applied
            })
    }

    private func startEdit(_ seg: KBDocSegment) {
        // Segments come from the snapshot while an edit is open, so their line
        // numbers may not match the draft: close the open edit first.
        guard inline == nil else { finishInline(); return }
        inline = InlineEdit(target: .block(lineStart: seg.lineStart, lineEnd: seg.lineEnd),
                            snapshot: source, original: seg.raw, buffer: seg.raw)
    }

    private func beginCellEdit(_ seg: KBDocSegment, row: Int, col: Int, value: String) {
        guard inline == nil else { finishInline(); return }
        guard row < seg.rowLines.count else { return }
        inline = InlineEdit(
            target: .cell(segmentID: seg.id, row: row, col: col, sourceLine: seg.rowLines[row]),
            snapshot: source, original: value, buffer: value)
    }

    private func editingCell(in seg: KBDocSegment) -> KBEditableTableView.CellKey? {
        guard case .cell(let id, let row, let col, _) = inline?.target, id == seg.id else { return nil }
        return KBEditableTableView.CellKey(row: row, col: col)
    }

    /// Close the editor. The change is already in the draft.
    private func finishInline() { inline = nil }

    /// Put the note back exactly as it was when the edit began.
    private func discardInline() {
        guard let edit = inline else { return }
        inline = nil
        source = edit.snapshot
    }

    // MARK: - Comment for Scout

    private func commentAffordance(_ seg: KBDocSegment) -> some View {
        Button { commentBuffer = ""; commentingOn = seg.id } label: {
            Label("Comment for Scout", systemImage: "bubble.left.and.text.bubble.right")
                .font(DS.sans(11)).foregroundStyle(DS.Ink.p3)
        }
        .buttonStyle(.plain)
        .help("Leave a note here for Scout to act on during its next dreaming session")
    }

    private func scoutCommentComposer(_ seg: KBDocSegment) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            TextEditor(text: $commentBuffer)
                .font(DS.sans(12.5)).foregroundStyle(DS.Ink.p1)
                .scrollContentBackground(.hidden)
                .frame(minHeight: 52)
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 6).fill(DS.Paper.sunk))
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Accent.fill.opacity(0.6), lineWidth: 1))
            HStack(spacing: 8) {
                Text("Scout reads this on its next dreaming run")
                    .font(DS.sans(10.5)).foregroundStyle(DS.Ink.p4)
                Spacer()
                Button("Cancel") { commentingOn = nil }
                    .buttonStyle(.plain).font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
                    .keyboardShortcut(.cancelAction)
                Button("Send to Scout") { submitComment(seg) }
                    .buttonStyle(.plain).font(DS.sans(12, weight: .semibold)).foregroundStyle(.white)
                    .padding(.horizontal, 12).padding(.vertical, 5)
                    .background(Capsule().fill(DS.Accent.fill))
                    .keyboardShortcut(.return, modifiers: .command)
                    .disabled(commentBuffer.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(.vertical, 4)
    }

    private func scoutCommentChip(_ seg: KBDocSegment) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "bubble.left.and.text.bubble.right")
                .font(.system(size: 10)).foregroundStyle(DS.Accent.ink)
            Text(ScoutMarker.body(of: seg.raw) ?? seg.raw)
                .font(DS.sans(12)).foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
            Text("for Scout · pending").font(DS.sans(10)).foregroundStyle(DS.Ink.p4)
            Spacer(minLength: 8)
            Button { retractComment(seg) } label: {
                Image(systemName: "xmark.circle.fill").font(.system(size: 12)).foregroundStyle(DS.Ink.p4)
            }
            .buttonStyle(.plain).help("Retract this comment (removes the marker)")
            // Its line numbers come from the snapshot while an edit is open.
            .disabled(inline != nil)
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 6).fill(DS.Accent.wash))
    }

    private func submitComment(_ seg: KBDocSegment) {
        guard let marker = ScoutMarker.format(commentBuffer) else { return }
        source = KBDocSegment.insertLine(in: source, afterLineEnd: seg.lineEnd, line: marker)
        commentingOn = nil
        commentBuffer = ""
        onRequestSave()
    }

    private func retractComment(_ seg: KBDocSegment) {
        source = KBDocSegment.removeLines(in: source, start: seg.lineStart, end: seg.lineEnd)
        onRequestSave()
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
            EmptyView()   // rendered as a chip in segmentView, never here
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
/// table but swaps the edited cell for a text field. The edit itself lives in
/// `KBEditableView.InlineEdit`, which writes each keystroke into that one cell
/// of the note's draft; Return and Esc both just close the field.
struct KBEditableTableView: View {
    let headers: [String]
    let rows: [[String]]
    /// The cell being edited, owned by the parent.
    let editingCell: CellKey?
    @Binding var buffer: String
    let onBeginEdit: (_ row: Int, _ col: Int, _ value: String) -> Void
    let onFinishEdit: () -> Void

    struct CellKey: Equatable { let row: Int; let col: Int }

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
        if editingCell == CellKey(row: r, col: c) {
            TextField("", text: $buffer)
                .textFieldStyle(.plain).font(DS.serif(12.5)).foregroundStyle(DS.Ink.p1)
                .padding(.horizontal, hPad - 2).padding(.vertical, 4)
                .background(RoundedRectangle(cornerRadius: 4).fill(DS.Paper.base))
                .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(DS.Accent.fill.opacity(0.6), lineWidth: 1))
                .frame(width: width, alignment: .topLeading)
                .onSubmit(onFinishEdit)
                .onExitCommand(perform: onFinishEdit)
        } else {
            InlineMarkdownText(value)
                .font(DS.serif(12.5)).foregroundStyle(DS.Ink.p2)
                .padding(.horizontal, hPad).padding(.vertical, 6)
                .frame(width: width, alignment: .topLeading)
                .contentShape(Rectangle())
                .onTapGesture(count: 2) { onBeginEdit(r, c, value) }
                .help("Double-click to edit")
        }
    }

    private func columnWidths() -> [CGFloat] {
        (0..<headers.count).map { c in
            var maxChars = headers[c].count
            for cells in rows where c < cells.count { maxChars = max(maxChars, cells[c].count) }
            return min(maxColumnWidth, max(minColumnWidth, CGFloat(maxChars) * charWidth)) + hPad * 2
        }
    }
}
