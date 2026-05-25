import SwiftUI

/// Editorial task entry. No card chrome — entries live on the page separated
/// by hairline rules. Layout: priority-dot gutter · content column · meta rail.
struct TaskCardView: View {
    let task: ActionTask
    let kind: ActionSection.Kind
    let displayedDate: Date
    let scoutDirectory: URL
    let onOp: (WriteOp) async throws -> Void

    @State private var inlineError: String?

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            gutter
            content
                .frame(maxWidth: .infinity, alignment: .leading)
            rail
                .frame(width: 140, alignment: .trailing)
        }
        .padding(.vertical, isNested ? 10 : 18)
        .padding(.leading, CGFloat(task.indentLevel) * 28)
        .overlay(alignment: .bottom) {
            // Nested entries share the parent's bottom rule — only the last
            // sibling needs its own. Top-level entries always get a rule.
            if !isNested { EditorialRule() }
        }
    }

    /// Sub-tasks (depth ≥ 1) get a lighter visual treatment so the parent–child
    /// relationship reads at a glance. Sized down, no halo, no meta rail.
    private var isNested: Bool { task.indentLevel > 0 }

    /// Kind used for visual treatment (gutter color, snooze action, etc.).
    /// Honors the source-section hint recorded by `scoutctl snooze --from-kind`
    /// so an urgent task that carries forward into the target day's `🛌 Snoozed`
    /// section stays visually urgent instead of demoting to `.neutral` gray.
    var effectiveKind: ActionSection.Kind { task.snoozedFromKind ?? kind }

    // MARK: - Gutter

    private var gutter: some View {
        VStack(spacing: 8) {
            Circle()
                .fill(DS.priorityColor(effectiveKind))
                .frame(width: isNested ? 6 : 10, height: isNested ? 6 : 10)
                .shadow(color: DS.priorityColor(effectiveKind).opacity(task.done ? 0 : 0.20), radius: 0, y: 0)
                .overlay {
                    if !isNested {
                        Circle()
                            .strokeBorder(DS.priorityColor(effectiveKind).opacity(task.done ? 0 : 0.20), lineWidth: 3)
                            .frame(width: 16, height: 16)
                    }
                }
                .opacity(task.done ? 0.55 : 1)
                .padding(.top, isNested ? 8 : 6)
            Rectangle()
                .fill(DS.Rule.soft)
                .frame(width: 1)
        }
        .frame(width: 22)
    }

    // MARK: - Content column

    private var content: some View {
        VStack(alignment: .leading, spacing: 10) {
            title

            if !task.body.isEmpty {
                InlineMarkdownText(task.body)
                    .font(DS.serif(13.5))
                    .foregroundStyle(DS.Ink.p2)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 640, alignment: .leading)
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

    private var title: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            InlineMarkdownText(task.subject)
                .font(DS.serif(isNested ? 13.5 : 15.5, weight: isNested ? .regular : .medium))
                .foregroundStyle(task.done ? DS.Ink.p3 : (isNested ? DS.Ink.p2 : DS.Ink.p1))
                .strikethrough(task.done, color: DS.Ink.p4)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 4)
            trailingStatus
        }
    }

    @ViewBuilder
    private var trailingStatus: some View {
        if task.done {
            statusPill("Done", color: DS.Status.ok)
        } else if let until = task.snoozedUntil {
            HStack(spacing: 3) {
                Image(systemName: "moon.zzz.fill")
                    .imageScale(.small)
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

    // MARK: - Meta rail

    private var rail: some View {
        VStack(alignment: .trailing, spacing: 4) {
            if let carried = task.carriedInFrom {
                railRow(key: "carry", value: dateShort(carried))
            }
            if let snoozed = task.snoozedUntil, !task.done {
                railRow(key: "until", value: dateShort(snoozed))
            }
            // Nested rows skip the line number — it's a power-user diagnostic
            // that only makes sense on top-level tasks. Removing it from
            // sub-items keeps the right rail visually quiet.
            if task.lineNumber > 0 && !isNested {
                railRow(key: "line", value: "\(task.lineNumber)")
            }
        }
        .padding(.top, 6)
    }

    @ViewBuilder
    private func railRow(key: String, value: String) -> some View {
        HStack(spacing: 6) {
            Text(key)
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p4)
            Text(value)
                .font(DS.mono(11, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
        }
    }

    // MARK: - Helpers

    /// Dispatches a write through `onOp` and threads any failure into the
    /// inline error label. Used by every mutation surface inside the card
    /// (actions row, comment composer, comment edit/delete) so the
    /// error-handling shape stays in one place.
    private func runOp(_ op: WriteOp) async {
        do {
            try await onOp(op)
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
