import AppKit
import SwiftUI
import Testing
@testable import Scout

@MainActor
private final class DraftCardLog {
    var listFocused = false
    var toggle: () -> Void = {}
}

/// One real card in a list wired as `ActionItemsView` wires it: the list's
/// focus sink, the keyboard context, and the hand-back of the keys when a
/// comment editor lets go of them.
private struct DraftCardProbe: View {
    let task: ActionTask
    let store: CommentDraftStore
    let log: DraftCardLog
    @State private var cursor: UUID?
    @State private var toggleRequest: ActionItemsKeyboardContext.ToggleRequest?
    @State private var serial = 0
    @State private var commentEditorFocused = false
    @FocusState private var listFocused: Bool

    var body: some View {
        VStack {
            TaskCardView(task: task, kind: .urgent, displayedDate: SmokeFixtures.day,
                         scoutDirectory: URL(fileURLWithPath: "/tmp/scout"), drafts: store, onOp: { _, _ in })
                .environment(\.openKBNote, StableAction { _ in })
            Spacer()
        }
        .padding(20)
        .environment(\.actionItemsKeyboard, ActionItemsKeyboardContext(
            cursorID: cursor,
            ringID: KeyboardListNavigation.ringed(cursor, in: [task.id]),
            commitKeyFree: !commentEditorFocused,
            toggleRequest: toggleRequest,
            focusCard: StableAction { id in
                cursor = id
                listFocused = true
            }))
        .onPreferenceChange(CommentEditorFocusedKey.self) { focused in
            if commentEditorFocused, !focused { listFocused = true }
            commentEditorFocused = focused
        }
        .dsFocusContainer($listFocused, showsCursor: listFocused && !commentEditorFocused)
        .onAppear {
            log.toggle = {
                serial += 1
                toggleRequest = .init(id: task.id, serial: serial)
            }
        }
        .onChange(of: listFocused) { _, focused in log.listFocused = focused }
    }
}

/// A kept comment draft through a real collapse: the card's detail, and with
/// it the composer, is torn down and built again.
@Suite("Comment drafts in a window", .serialized, .timeLimit(.minutes(1)))
@MainActor
struct CommentDraftWindowTests {
    private func open(_ view: some View) async throws -> NSWindow {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 520),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = NSHostingView(rootView: view.frame(width: 560, height: 520))
        window.makeKeyAndOrderFront(nil)
        try await Task.sleep(for: .milliseconds(400))
        return window
    }

    private func settle(_ ms: Int = 400) async throws { try await Task.sleep(for: .milliseconds(ms)) }

    /// The comment editors on screen: editable text views, not field editors.
    private func editors(in view: NSView?) -> [NSTextView] {
        guard let view else { return [] }
        var found: [NSTextView] = []
        if let text = view as? NSTextView, text.isEditable, !text.isFieldEditor { found.append(text) }
        for sub in view.subviews { found += editors(in: sub) }
        return found
    }

    private func describe(_ responder: NSResponder?) -> String {
        responder.map { String(describing: type(of: $0)) } ?? "nothing"
    }

    @Test("type a draft, collapse the card, open it again: the text is still in the composer")
    func collapseAndReopenKeepsTheText() async throws {
        try await KeyWindowGate.run {
            let store = CommentDraftStore()
            let log = DraftCardLog()
            let task = SmokeFixtures.task(subject: "Reply to Priya", body: "About the rollout.")
            // Opens the composer, as a draft typed before would.
            store.update("Hello", for: CommentComposerView.draftKey(task, SmokeFixtures.day), task: task)
            let window = try await open(DraftCardProbe(task: task, store: store, log: log))
            defer { window.close() }

            let editor = try #require(editors(in: window.contentView).first, "the composer didn't open on the draft")
            #expect(editor.string == "Hello")
            window.makeFirstResponder(editor)
            editor.setSelectedRange(NSRange(location: editor.string.utf16.count, length: 0))
            WindowEvents.type(" world", in: window)
            try await settle()
            let key = CommentComposerView.draftKey(task, SmokeFixtures.day)
            #expect(store.text(for: key) == "Hello world", "typing didn't reach the kept draft")

            log.toggle()
            try await settle(600)
            #expect(editors(in: window.contentView).isEmpty, "the card didn't collapse")
            #expect(store.text(for: key) == "Hello world", "collapsing dropped the draft")

            log.toggle()
            try await settle(600)
            let reopened = editors(in: window.contentView)
            #expect(reopened.map(\.string) == ["Hello world"], "opening the card again lost the text")
        }
    }

    @Test("collapsing a card whose comment editor has the keys hands them to the list: no stop without a ring")
    func collapseLeavesNoRinglessStop() async throws {
        try await KeyWindowGate.run {
            let store = CommentDraftStore()
            let log = DraftCardLog()
            let task = SmokeFixtures.task(subject: "Reply to Priya", body: "About the rollout.")
            store.update("Hello", for: CommentComposerView.draftKey(task, SmokeFixtures.day), task: task)
            let window = try await open(DraftCardProbe(task: task, store: store, log: log))
            defer { window.close() }

            let editor = try #require(editors(in: window.contentView).first, "the composer didn't open on the draft")
            window.makeFirstResponder(editor)
            try await settle()
            try #require(window.firstResponder === editor, "the editor didn't take the keys")

            log.toggle()
            try await settle(600)
            let responder = window.firstResponder
            #expect(!(responder is NSTextView), "the collapsed card's editor kept the keys: \(describe(responder))")
            if let view = responder as? NSView {
                #expect(view.window === window, "the keys are on a view no longer in the window: \(describe(view))")
            }
            #expect(log.listFocused, "the list didn't take the keys: they're on \(describe(responder))")
            // Every stop Tab reaches is on screen: none is the removed editor.
            for _ in 0..<6 {
                WindowEvents.press(WindowEvents.tab, in: window)
                try await settle(150)
                let stop = window.firstResponder
                #expect(!(stop is NSTextView), "Tab reached a comment editor on a collapsed card")
                if let view = stop as? NSView {
                    #expect(view.window === window, "Tab reached a view no longer in the window: \(describe(view))")
                }
            }
        }
    }
}
