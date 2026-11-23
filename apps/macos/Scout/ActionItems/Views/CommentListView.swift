import SwiftUI

/// Inline threaded comments under a task. Editorial voice: `> scout` for
/// SCOUT-generated lines, `// user` for human replies, in mono; comment
/// body in serif. Sits in a sunk panel with a left hairline rule.
///
/// Each comment row exposes hover-revealed edit/delete affordances backed
/// by `scoutctl action-items edit-comment` / `delete-comment`. The 1-based
/// `index` here counts the comments shown; the card maps it to scoutctl's
/// `--index`, which also counts `/scout-plan` markers, through
/// `ActionTask.commentSelector(at:)`.
struct CommentListView: View {
    let comments: [TaskComment]
    /// Invoked when the user clicks edit on a comment. The closure receives
    /// the 1-based index of the comment and the new body text.
    var onEdit: ((Int, String) async -> Bool)? = nil
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
    let onEdit: ((Int, String) async -> Bool)?
    let onDelete: ((Int) async -> Void)?

    @State private var hovering = false
    @State private var editing = false
    @State private var draft = ""
    @State private var submitting = false
    @State private var confirmingDelete = false
    @FocusState private var editorFocused: Bool
    @Environment(\.cardClickFocus) private var clickFocus

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(markGlyph(for: comment.author))
                .font(DS.font(.caption, .mono, weight: .medium))
                .foregroundStyle(DS.Ink.p4)
                .padding(.top, 2)
                .frame(width: 14, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(comment.author)
                        .font(DS.font(.label, weight: .medium))
                        .foregroundStyle(authorColor(comment.author))
                    if !comment.timestamp.isEmpty {
                        Text(comment.timestamp)
                            .font(DS.font(.caption, .mono))
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
                        .dsFont(.body, .serif)
                        .foregroundStyle(DS.Ink.p3)
                        .lineSpacing(2)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
            }
        }
        .onKeyWindowHover { hovering = $0 }
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
                    // The editor takes the keys, not the list the click is on.
                    clickFocus?.claimKeys()
                    draft = comment.text
                    editing = true
                    DispatchQueue.main.async { editorFocused = true }
                } label: {
                    Image(systemName: "pencil")
                        .font(DS.font(.caption2))
                }
                .buttonStyle(.plainHit)
                .foregroundStyle(DS.Ink.p3)
                .help("Edit comment")
                .accessibilityLabel("Edit comment")
            }
            if onDelete != nil {
                Button {
                    confirmingDelete = true
                } label: {
                    Image(systemName: "trash")
                        .font(DS.font(.caption2))
                }
                .buttonStyle(.plainHit)
                .foregroundStyle(DS.Ink.p3)
                .help("Delete comment")
                .accessibilityLabel("Delete comment")
            }
        }
    }

    private var editor: some View {
        VStack(alignment: .leading, spacing: 6) {
            CommentTextEditor(text: $draft, focus: $editorFocused, minHeight: 28)
            HStack(spacing: 4) {
                Spacer()
                EditorialActionButton("Cancel") {
                    draft = ""
                    editing = false
                }
                // ⌘↵ saves only while this editor has focus (see the composer).
                EditorialActionButton(
                    "Save", style: .primary,
                    keyboardShortcut: editorFocused ? AppShortcut.commit : nil
                ) { performSave() }
                    .disabled(
                        submitting ||
                        draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
                        draft == comment.text
                    )
            }
        }
    }

    private func performSave() {
        let trimmed = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !submitting, trimmed != comment.text, let onEdit else { return }
        submitting = true
        Task { @MainActor in
            // A failed write keeps the editor open with the text intact.
            let landed = await onEdit(index, trimmed)
            submitting = false
            guard landed else { return }
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
        let userName = CommentAuthor.configuredName()
        if CommentAuthor.isOwn(commentAuthor: author, userName: userName) { return "//" }
        return "·"
    }

    private func authorColor(_ author: String) -> Color {
        let a = author.lowercased()
        if a == "scout" || a.contains("briefing") || a.contains("dreaming") { return DS.Accent.ink }
        return DS.Ink.p2
    }
}
