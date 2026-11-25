import SwiftUI

/// Collapsed-by-default composer. Most task cards don't need an editor
/// allocated — LazyVGrid ends up instantiating a ``TextEditor`` (NSTextView
/// under the hood) per open task, which stalls scroll on a full day. The
/// button is cheap; only once the user clicks does the editor materialize.
///
/// The text is kept in `CommentDraftStore`, not here: collapsing the card
/// tears this view down, and a draft held only in its `@State` went with it
/// without a word. Built again, the composer opens on the kept draft.
struct CommentComposerView: View {
    let task: ActionTask
    let displayedDate: Date
    /// Writes the comment; returns whether it landed. On false the composer
    /// keeps the text so a failed write never eats what the user typed.
    let onSubmit: (String) async -> Bool
    private let drafts: CommentDraftStore
    /// The kept draft's write, from the store: a composer built again while
    /// one is on its way doesn't offer Send twice.
    private let sending: CommentSendState

    @State private var state: CommentDraftState
    @FocusState private var editorFocused: Bool
    @Environment(\.openSettings) private var openSettings
    @Environment(\.cardClickFocus) private var clickFocus
    /// Read so the "Set your name" link goes away as soon as one is set.
    @AppStorage(CommentAuthor.defaultsKey) private var authorName: String = ""

    init(task: ActionTask, displayedDate: Date, drafts: CommentDraftStore = .shared,
         onSubmit: @escaping (String) async -> Bool) {
        self.task = task
        self.displayedDate = displayedDate
        self.drafts = drafts
        self.onSubmit = onSubmit
        let key = Self.draftKey(task, displayedDate)
        sending = drafts.sendState(for: key)
        _state = State(initialValue: CommentDraftState.restored(drafts.text(for: key)))
    }

    static func draftKey(_ task: ActionTask, _ displayedDate: Date) -> CommentDraftKey {
        CommentDraftKey(task: CommentDraftTaskKey(task: task, displayedDate: displayedDate), target: .new)
    }

    private var draftKey: CommentDraftKey { Self.draftKey(task, displayedDate) }

    private var expanded: Bool { state.expanded }

    var body: some View {
        if expanded {
            expandedEditor
                .onChange(of: state.text) { _, text in drafts.update(text, for: draftKey, task: task) }
                // A Send begun before the card collapsed landed: close as
                // its own composer would have.
                .onChange(of: sending.landed) { state.finishSubmit(succeeded: true) }
        } else {
            EditorialActionButton("Add comment", systemImage: "text.bubble") {
                // The editor takes the keys, not the list the click is on.
                clickFocus?.claimKeys()
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
                // ⌘↵ sends only while this editor has focus: another card's
                // composer, or Done on the keyboard-current card, owns it
                // otherwise (`commentEditorFocused`).
                EditorialActionButton(
                    "Send", style: .primary,
                    keyboardShortcut: editorFocused ? AppShortcut.commit : nil
                ) { submit() }
                    .disabled(!state.canSubmit || sending.inFlight)
            }
        }
    }

    private func cancel() {
        Self.cancel(&state, drafts: drafts, key: draftKey)
    }

    private func submit() {
        guard !sending.inFlight, let text = state.beginSubmit() else { return }
        // Captured, so a write that lands after the card collapsed still
        // clears the kept draft: this view's state is gone by then.
        let drafts = drafts, key = draftKey, onSubmit = onSubmit
        Task { @MainActor in
            let landed = await Self.send(text, drafts: drafts, key: key, onSubmit: onSubmit)
            state.finishSubmit(succeeded: landed)
        }
    }

    /// Cancel: the editor closes and the kept draft goes.
    static func cancel(_ state: inout CommentDraftState, drafts: CommentDraftStore, key: CommentDraftKey) {
        state.cancel()
        drafts.discard(key)
    }

    /// Send: writes `text` and, once it lands, drops the kept draft. A failed
    /// write keeps it, to retry or copy. While one is on its way a second is
    /// refused (`CommentDraftStore.send`).
    static func send(_ text: String, drafts: CommentDraftStore, key: CommentDraftKey,
                     onSubmit: (String) async -> Bool) async -> Bool {
        await drafts.send(text, for: key, through: onSubmit)
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

    /// The composer as it opens: on a kept draft, expanded with its text;
    /// without one, closed.
    static func restored(_ kept: String?) -> CommentDraftState {
        guard let kept else { return CommentDraftState() }
        return CommentDraftState(text: kept, expanded: true)
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
            .dsFont(.body, .serif)
            .foregroundStyle(DS.Ink.p1)
            .focused(focus)
            .scrollContentBackground(.hidden)
            .padding(.horizontal, 6)
            .padding(.vertical, 4)
            .frame(minHeight: minHeight, maxHeight: 120)
            .neumorphicPressed(cornerRadius: 5)
            .preference(key: CommentEditorFocusedKey.self, value: focus.wrappedValue)
    }
}

/// True while any comment editor on the page has keyboard focus. Action Items
/// reads it so ⌘↵ goes to that editor's Send or Save, not to Done on the
/// keyboard-current card: two buttons bound to one key would fire either.
struct CommentEditorFocusedKey: PreferenceKey {
    static let defaultValue = false
    static func reduce(value: inout Bool, nextValue: () -> Bool) {
        value = value || nextValue()
    }
}
