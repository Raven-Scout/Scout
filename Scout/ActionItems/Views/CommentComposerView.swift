import SwiftUI

/// Collapsed-by-default composer. Most task cards don't need an editor
/// allocated — LazyVGrid ends up instantiating a ``TextEditor`` (NSTextView
/// under the hood) per open task, which stalls scroll on a full day. The
/// button is cheap; only once the user clicks does the editor materialize.
struct CommentComposerView: View {
    let task: ActionTask
    let displayedDate: Date
    let onSubmit: (String) async -> Void

    @State private var expanded = false
    @State private var draft: String = ""
    @State private var submitting = false
    @FocusState private var editorFocused: Bool

    var body: some View {
        if expanded {
            expandedEditor
        } else {
            EditorialActionButton("Add comment", systemImage: "text.bubble") {
                expanded = true
                DispatchQueue.main.async { editorFocused = true }
            }
        }
    }

    private var expandedEditor: some View {
        VStack(alignment: .leading, spacing: 6) {
            CommentTextEditor(text: $draft, focus: $editorFocused, minHeight: 32)
            HStack(spacing: 4) {
                Spacer()
                EditorialActionButton("Cancel") { cancel() }
                EditorialActionButton(
                    "Send", style: .primary, shortcut: "⌘↵",
                    keyboardShortcut: KeyboardShortcut(.return, modifiers: .command)
                ) { submit() }
                    .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || submitting)
            }
        }
    }

    private func cancel() {
        draft = ""
        expanded = false
    }

    private func submit() {
        let trimmed = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !submitting else { return }
        submitting = true
        let text = trimmed
        draft = ""
        Task { @MainActor in
            await onSubmit(text)
            submitting = false
            expanded = false
        }
    }
}

/// The comment text field: serif like the comment bodies, on the recessed input
/// surface. Shared by the composer and the inline comment editor.
struct CommentTextEditor: View {
    @Binding var text: String
    let focus: FocusState<Bool>.Binding
    var minHeight: CGFloat = 32

    var body: some View {
        TextEditor(text: $text)
            .font(DS.serif(13))
            .foregroundStyle(DS.Ink.p1)
            .focused(focus)
            .scrollContentBackground(.hidden)
            .padding(.horizontal, 6)
            .padding(.vertical, 4)
            .frame(minHeight: minHeight, maxHeight: 120)
            .neumorphicPressed(cornerRadius: 5)
    }
}
