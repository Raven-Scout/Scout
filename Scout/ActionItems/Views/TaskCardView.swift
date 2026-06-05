import SwiftUI

/// A task in the Action Items List. Top-level tasks render as a collapsible
/// card — a scannable header (priority stripe · prefix · title · source chips ·
/// quick actions) with the dense detail (body, comments, full actions, comment
/// composer) tucked behind a chevron. Urgent tasks start expanded; everything
/// else starts collapsed. Nested sub-tasks render as light indented rows.
///
/// Inspired by the triage artifact's card + progressive-disclosure layout,
/// keeping Scout's editorial palette.
struct TaskCardView: View {
    let task: ActionTask
    let kind: ActionSection.Kind
    let displayedDate: Date
    let scoutDirectory: URL
    let onOp: (WriteOp, Int?) async throws -> Void

    @State private var inlineError: String?
    @State private var expanded: Bool
    @State private var showingQuickSnooze = false

    init(
        task: ActionTask,
        kind: ActionSection.Kind,
        displayedDate: Date,
        scoutDirectory: URL,
        onOp: @escaping (WriteOp, Int?) async throws -> Void
    ) {
        self.task = task
        self.kind = kind
        self.displayedDate = displayedDate
        self.scoutDirectory = scoutDirectory
        self.onOp = onOp
        // Urgent opens by default — its detail is what you want immediately.
        _expanded = State(initialValue: (task.snoozedFromKind ?? kind) == .urgent)
    }

    var body: some View {
        if isNested {
            nestedRow
        } else {
            card
        }
    }

    /// Sub-tasks (depth ≥ 1) stay lightweight — no card chrome, no collapse,
    /// indented under their parent so the hierarchy reads at a glance.
    private var isNested: Bool { task.indentLevel > 0 }

    /// Kind used for visual treatment. Honors the source-section hint recorded
    /// by `scoutctl snooze --from-kind` so an urgent task that carries forward
    /// into the `🛌 Snoozed` section stays visually urgent.
    var effectiveKind: ActionSection.Kind { task.snoozedFromKind ?? kind }

    // MARK: - Card

    private var card: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if expanded {
                detail
                    .padding(.horizontal, 14)
                    .padding(.bottom, 14)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        )
        .overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 2)
                .fill(DS.priorityColor(effectiveKind))
                .frame(width: 3)
                .padding(.vertical, 10)
                .opacity(task.done ? 0.5 : 1)
        }
        .padding(.bottom, 10)
    }

    // MARK: - Header (always visible, scannable)

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if let prefix = task.shortPrefix {
                    Text("#\(prefix)")
                        .font(DS.mono(10.5, weight: .medium))
                        .foregroundStyle(DS.Ink.p4)
                }
                InlineMarkdownText(task.subject)
                    .font(DS.serif(15.5, weight: .medium))
                    .foregroundStyle(task.done ? DS.Ink.p3 : DS.Ink.p1)
                    .strikethrough(task.done, color: DS.Ink.p4)
                    .lineLimit(expanded ? nil : 2)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                    .onTapGesture { toggle() }
                if !expanded { quickActions }
                trailingStatus
                chevron
            }
            if !chips.isEmpty {
                chipRow
            }
        }
        .padding(14)
        .contentShape(Rectangle())
    }

    private var chevron: some View {
        Button { toggle() } label: {
            Image(systemName: expanded ? "chevron.up" : "chevron.down")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(DS.Ink.p4)
                .frame(width: 22, height: 22)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plainHit)
    }

    private func toggle() { withAnimation(.easeInOut(duration: 0.15)) { expanded.toggle() } }

    // MARK: - Source/context chips

    private var chips: [TaskChip] {
        TaskChip.chips(
            for: task,
            carriedLabel: task.carriedInFrom.map { dateShort($0) }
        )
    }

    private var chipRow: some View {
        HStack(spacing: 6) {
            ForEach(chips) { chip in
                HStack(spacing: 4) {
                    Image(systemName: chipGlyph(chip.glyph))
                        .font(.system(size: 9))
                    Text(chip.label)
                        .font(DS.mono(10.5))
                        .lineLimit(1)
                }
                .foregroundStyle(DS.Ink.p3)
                .padding(.horizontal, 7)
                .padding(.vertical, 2)
                .background(EditorialChipBackground())
            }
        }
    }

    private func chipGlyph(_ glyph: TaskChip.Glyph) -> String {
        switch glyph {
        case .github: return "arrow.triangle.pull"
        case .linear: return "circle.grid.2x2"
        case .slack:  return "bubble.left.and.bubble.right"
        case .carry:  return "calendar"
        }
    }

    // MARK: - Quick actions (collapsed only)

    private var quickActions: some View {
        HStack(spacing: 4) {
            if task.done {
                iconButton("arrow.uturn.backward", help: "Reopen") {
                    Task { await runOp(.reopen(subject: task.matchableSubject, shortPrefix: task.shortPrefix)) }
                }
            } else {
                iconButton("checkmark", help: "Mark done") {
                    Task { await runOp(.markDone(subject: task.matchableSubject, shortPrefix: task.shortPrefix)) }
                }
                iconButton("moon.zzz", help: "Snooze") { showingQuickSnooze = true }
                    .popover(isPresented: $showingQuickSnooze) {
                        SnoozePopoverView(sourceDate: displayedDate) { target in
                            await runOp(.snooze(
                                subject: task.matchableSubject,
                                shortPrefix: task.shortPrefix,
                                until: target,
                                fromKind: kind.rawValue
                            ))
                            showingQuickSnooze = false
                        } onCancel: {
                            showingQuickSnooze = false
                        }
                    }
            }
        }
    }

    private func iconButton(_ systemImage: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.system(size: 11))
                .foregroundStyle(DS.Ink.p3)
                .frame(width: 24, height: 22)
                .background(RoundedRectangle(cornerRadius: 5).fill(DS.Paper.base))
                .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plainHit)
        .help(help)
    }

    @ViewBuilder
    private var trailingStatus: some View {
        if task.done {
            statusPill("Done", color: DS.Status.ok)
        } else if let until = task.snoozedUntil {
            HStack(spacing: 3) {
                Image(systemName: "moon.zzz.fill").imageScale(.small)
                Text(dateShort(until))
            }
            .font(DS.mono(10.5))
            .foregroundStyle(DS.Ink.p3)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(EditorialChipBackground())
        }
    }

    private func statusPill(_ label: String, color: Color) -> some View {
        Text(label)
            .font(DS.mono(10.5, weight: .medium))
            .foregroundStyle(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(EditorialChipBackground())
    }

    // MARK: - Expanded detail

    private var detail: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !task.body.isEmpty {
                TaskBodyView(rawBody: task.body)
            }

            if !task.comments.isEmpty {
                CommentListView(
                    comments: task.comments,
                    onEdit: { index, newText in
                        await runOp(.editComment(
                            subject: task.matchableSubject,
                            shortPrefix: task.shortPrefix,
                            selector: .index(index),
                            newText: newText
                        ))
                    },
                    onDelete: { index in
                        await runOp(.deleteComment(
                            subject: task.matchableSubject,
                            shortPrefix: task.shortPrefix,
                            selector: .index(index)
                        ))
                    }
                )
            }

            if !task.deepLinks.isEmpty {
                TaskLinksView(links: task.deepLinks)
            }

            TaskActionsView(
                task: task,
                kind: effectiveKind,
                displayedDate: displayedDate,
                scoutDirectory: scoutDirectory
            ) { op in
                await runOp(op)
            }

            if !task.done {
                CommentComposerView(task: task, displayedDate: displayedDate) { text in
                    let author = UserDefaults.standard.string(forKey: "authorName") ?? "user"
                    await runOp(.addComment(
                        subject: task.matchableSubject,
                        shortPrefix: task.shortPrefix,
                        text: text,
                        author: author
                    ))
                }
            }

            if let err = inlineError {
                Text(err)
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Status.err)
                    .padding(.top, 2)
            }
        }
    }

    // MARK: - Nested sub-task row

    private var nestedRow: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(DS.priorityColor(effectiveKind))
                .frame(width: 5, height: 5)
                .opacity(task.done ? 0.5 : 0.8)
                .padding(.top, 7)
            VStack(alignment: .leading, spacing: 4) {
                InlineMarkdownText(task.subject)
                    .font(DS.serif(13.5))
                    .foregroundStyle(task.done ? DS.Ink.p3 : DS.Ink.p2)
                    .strikethrough(task.done, color: DS.Ink.p4)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if !task.body.isEmpty {
                    InlineMarkdownText(task.body)
                        .font(DS.serif(12.5))
                        .foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .padding(.vertical, 6)
        .padding(.leading, CGFloat(task.indentLevel) * 24 + 16)
        .padding(.trailing, 14)
    }

    // MARK: - Helpers

    /// Dispatches a write through `onOp` and threads any failure into the
    /// inline error label.
    private func runOp(_ op: WriteOp) async {
        do {
            try await onOp(op, task.lineNumber)
            await MainActor.run { inlineError = nil }
        } catch let err as ActionItemsWriterError {
            await MainActor.run { inlineError = describe(err) }
        } catch {
            await MainActor.run { inlineError = error.localizedDescription }
        }
    }

    private func describe(_ err: ActionItemsWriterError) -> String {
        switch err {
        case .cliNonZeroExit(_, let stderr, let kind):
            switch kind {
            case .noMatch:     return "Task may have been edited externally — refreshing.\n\(stderr)"
            case .ambiguous:   return "Subject matched multiple tasks.\n\(stderr)"
            case .environment: return "Python environment problem.\n\(stderr)"
            case .other:       return stderr.isEmpty ? "Write failed." : stderr
            }
        case .processFailed(let e):
            return "Process failed: \(e.localizedDescription)"
        }
    }

    private func dateShort(_ d: Date) -> String {
        let fmt = DateFormatter(); fmt.dateFormat = "MMM d"; fmt.timeZone = TimeZone(identifier: "America/New_York")
        return fmt.string(from: d)
    }
}
