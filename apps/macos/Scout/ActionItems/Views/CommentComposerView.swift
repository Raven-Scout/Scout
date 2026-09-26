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
            Button {
                expanded = true
                DispatchQueue.main.async { editorFocused = true }
            } label: {
                Label("Add comment", systemImage: "text.bubble")
                    .font(.caption)
            }
            .buttonStyle(.borderless)
            .foregroundStyle(.secondary)
        }
    }

    private var expandedEditor: some View {
        VStack(alignment: .leading, spacing: 4) {
            TextEditor(text: $draft)
                .font(.system(size: 12))
                .focused($editorFocused)
                .scrollContentBackground(.hidden)
                .padding(4)
                .frame(minHeight: 32, maxHeight: 120)
                .overlay(
                    RoundedRectangle(cornerRadius: 5)
                        .stroke(Color.secondary.opacity(0.3))
                )
            HStack {
                Text("⌘+Return to send")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                Spacer()
                Button("Cancel") { cancel() }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                Button("Send") { submit() }
                    .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || submitting)
                    .keyboardShortcut(.return, modifiers: .command)
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
