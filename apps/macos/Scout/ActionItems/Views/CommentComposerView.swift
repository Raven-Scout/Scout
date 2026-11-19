import SwiftUI

/// Collapsed-by-default composer. Most task cards don't need an editor
/// allocated — LazyVGrid ends up instantiating a ``TextEditor`` (NSTextView
/// under the hood) per open task, which stalls scroll on a full day. The
/// button is cheap; only once the user clicks does the editor materialize.
struct CommentComposerView: View {
    let task: ActionTask
    let displayedDate: Date
    /// Writes the comment; returns whether it landed. On false the composer
    /// keeps the text so a failed write never eats what the user typed.
    let onSubmit: (String) async -> Bool

    @State private var state = CommentDraftState()
    @FocusState private var editorFocused: Bool
    @Environment(\.openSettings) private var openSettings
    /// Read so the "Set your name" link goes away as soon as one is set.
    @AppStorage(CommentAuthor.defaultsKey) private var authorName: String = ""

    private var expanded: Bool { state.expanded }

    var body: some View {
        if expanded {
            expandedEditor
        } else {
            EditorialActionButton("Add comment", systemImage: "text.bubble") {
                state.expanded = true
                DispatchQueue.main.async { editorFocused = true }
            }
        }
    }

    private var expandedEditor: some View {
        VStack(alignment: .leading, spacing: 6) {
            CommentTextEditor(text: $state.text, focus: $editorFocused, minHeight: 32)
            HStack(spacing: 4) {
                if !CommentAuthor.isNameSet(authorName: authorName) {
                    // UI audit #85: say who the comment will be signed as, and
                    // where to change it, while Scout doesn't know the name.
                    Button("Signed \u{201C}\(CommentAuthor.fallback)\u{201D} · Set your name…") {
                        HelpActions.showSettings(openSettings, at: SettingsAnchor.authorship)
                    }
                    .buttonStyle(.ds(.link, size: .small))
                    .accessibilityHint("Opens Settings at Your name")
                }
                Spacer()
                EditorialActionButton("Cancel") { cancel() }
                EditorialActionButton(
                    "Send", style: .primary, shortcut: "⌘↵",
                    keyboardShortcut: KeyboardShortcut(.return, modifiers: .command)
                ) { submit() }
                    .disabled(!state.canSubmit)
            }
        }
    }

    private func cancel() {
        state.cancel()
    }

    private func submit() {
        guard let text = state.beginSubmit() else { return }
        Task { @MainActor in
            let landed = await onSubmit(text)
            state.finishSubmit(succeeded: landed)
        }
    }
}

/// The composer's draft. The text is cleared only once the write lands; a
/// failed send leaves it in the open editor to retry or copy.
struct CommentDraftState: Equatable {
    var text = ""
    var expanded = false
    private(set) var submitting = false

    init(text: String = "", expanded: Bool = false) {
        self.text = text
        self.expanded = expanded
    }

    var canSubmit: Bool {
        !submitting && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Marks the draft as sending and returns the text to write, or nil when
    /// there is nothing to send. Does not clear the text.
    mutating func beginSubmit() -> String? {
        guard canSubmit else { return nil }
        submitting = true
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    mutating func finishSubmit(succeeded: Bool) {
        submitting = false
        guard succeeded else { return }
        text = ""
        expanded = false
    }

    mutating func cancel() {
        text = ""
        expanded = false
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
