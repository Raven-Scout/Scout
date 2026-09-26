import SwiftUI

/// Inline threaded comments under a task. Editorial voice: `> scout` for
/// SCOUT-generated lines, `// user` for human replies, in mono; comment
/// body in serif. Sits in a sunk panel with a left hairline rule.
///
/// Each comment row exposes hover-revealed edit/delete affordances backed
/// by `scoutctl action-items edit-comment` / `delete-comment`. The 1-based
/// `index` here mirrors scoutctl's `--index` selector — the parser filters
/// out the `snoozed-until` marker for the same reason.
struct CommentListView: View {
    let comments: [TaskComment]
    /// Invoked when the user clicks edit on a comment. The closure receives
    /// the 1-based index of the comment and the new body text.
    var onEdit: ((Int, String) async -> Void)? = nil
    /// Invoked when the user clicks delete on a comment. The closure
    /// receives the 1-based index of the comment.
    var onDelete: ((Int) async -> Void)? = nil

    var body: some View {
        if comments.isEmpty {
            EmptyView()
        } else {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Array(comments.enumerated()), id: \.offset) { index, c in
                    CommentRow(
                        index: index + 1,
                        comment: c,
                        onEdit: onEdit,
                        onDelete: onDelete
                    )
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DS.Paper.sunk.opacity(0.6))
            .overlay(alignment: .leading) {
                Rectangle().fill(DS.Rule.hard).frame(width: 2)
            }
            .clipShape(RoundedRectangle(cornerRadius: 6))
        }
    }
}

private struct CommentRow: View {
    let index: Int
    let comment: TaskComment
    let onEdit: ((Int, String) async -> Void)?
    let onDelete: ((Int) async -> Void)?

    @State private var hovering = false
    @State private var editing = false
    @State private var draft = ""
    @State private var submitting = false
    @State private var confirmingDelete = false
    @FocusState private var editorFocused: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(markGlyph(for: comment.author))
                .font(DS.mono(11, weight: .medium))
                .foregroundStyle(DS.Ink.p4)
                .padding(.top, 2)
                .frame(width: 14, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(comment.author)
                        .font(DS.sans(12, weight: .medium))
                        .foregroundStyle(authorColor(comment.author))
                    if !comment.timestamp.isEmpty {
                        Text(comment.timestamp)
                            .font(DS.mono(11))
                            .foregroundStyle(DS.Ink.p4)
                    }
                    Spacer(minLength: 8)
                    if hovering && !editing, (onEdit != nil || onDelete != nil) {
                        actions
                    }
                }
                if editing {
                    editor
                } else {
                    InlineMarkdownText(comment.text)
                        .font(DS.serif(13))
                        .foregroundStyle(DS.Ink.p3)
                        .lineSpacing(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .onHover { hovering = $0 }
        .alert("Delete this comment?", isPresented: $confirmingDelete) {
            Button("Delete", role: .destructive) { performDelete() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Removes the line from today's markdown. Git history keeps it.")
        }
    }

    private var actions: some View {
        HStack(spacing: 4) {
            if onEdit != nil {
                Button {
                    draft = comment.text
                    editing = true
                    DispatchQueue.main.async { editorFocused = true }
                } label: {
                    Image(systemName: "pencil")
                        .font(.system(size: 10))
                }
                .buttonStyle(.borderless)
                .foregroundStyle(.secondary)
                .help("Edit comment")
            }
            if onDelete != nil {
                Button {
                    confirmingDelete = true
                } label: {
                    Image(systemName: "trash")
                        .font(.system(size: 10))
                }
                .buttonStyle(.borderless)
                .foregroundStyle(.secondary)
                .help("Delete comment")
            }
        }
    }

    private var editor: some View {
        VStack(alignment: .leading, spacing: 4) {
            TextEditor(text: $draft)
                .font(.system(size: 12))
                .focused($editorFocused)
                .scrollContentBackground(.hidden)
                .padding(4)
                .frame(minHeight: 28, maxHeight: 120)
                .overlay(
                    RoundedRectangle(cornerRadius: 5)
                        .stroke(Color.secondary.opacity(0.3))
                )
            HStack {
                Text("⌘+Return to save")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                Spacer()
                Button("Cancel") {
                    draft = ""
                    editing = false
                }
                .buttonStyle(.borderless)
                .foregroundStyle(.secondary)
                Button("Save") { performSave() }
                    .disabled(
                        submitting ||
                        draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
                        draft == comment.text
                    )
                    .keyboardShortcut(.return, modifiers: .command)
            }
        }
    }

    private func performSave() {
        let trimmed = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !submitting, trimmed != comment.text, let onEdit else { return }
        submitting = true
        Task { @MainActor in
            await onEdit(index, trimmed)
            submitting = false
            editing = false
            draft = ""
        }
    }

    private func performDelete() {
        guard !submitting, let onDelete else { return }
        submitting = true
        Task { @MainActor in
            await onDelete(index)
            submitting = false
        }
    }

    private func markGlyph(for author: String) -> String {
        let a = author.lowercased()
        if a == "scout" || a.contains("briefing") || a.contains("dreaming") { return ">" }
        let userAuthor = (UserDefaults.standard.string(forKey: "authorName") ?? "user").lowercased()
        if a == userAuthor { return "//" }
        return "·"
    }

    private func authorColor(_ author: String) -> Color {
        let a = author.lowercased()
        if a == "scout" || a.contains("briefing") || a.contains("dreaming") { return DS.Accent.ink }
        return DS.Ink.p2
    }
}
