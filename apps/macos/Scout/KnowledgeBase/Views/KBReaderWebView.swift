import SwiftUI
import Combine
import AppKit

/// A note in Read mode (`kb-reader.html`): rendered by markdown-it, but still
/// the note's source underneath. Double-click a block or table cell to edit it
/// in place; select any text and click Comment (⇧⌘M) to leave Scout a comment
/// about it; click a checkbox to tick a task. Every change is applied to
/// `source` by `KBReaderDocument`, line for line, so the rest of the note is
/// untouched.
///
/// Block edits go into the draft as you type and are saved with ⌘S, like any
/// other edit. A comment, or retracting one, saves at once (`onRequestSave`),
/// as Scout's comments always have.
struct KBReaderWebView: View {
    @Binding var source: String
    /// Resolves a wikilink target to a note path, or nil when there's no note.
    let resolve: (String) -> String?
    var onRequestSave: () -> Void = {}

    @Environment(\.kbWikilinkHandler) private var kbWikilinkHandler
    @Environment(\.kbTagHandler) private var kbTagHandler
    @Environment(\.openKBNote) private var openKBNote

    @StateObject private var bridge = KBWebBridge(page: "kb-reader")
    @StateObject private var session = Session()

    /// Reader state that must not trigger a SwiftUI update when it changes.
    @MainActor final class Session: ObservableObject {
        var inline: KBInlineEdit?
        /// The source the page last rendered, so an update that changes
        /// nothing (or is the open edit's own keystroke) isn't re-sent.
        var rendered: String?
        /// Bumped by every render. The page echoes the version it shows on
        /// each change it asks for, and one made against an older render is
        /// dropped: its line numbers describe a note that has since changed.
        var version = 0
    }

    var body: some View {
        KBWebViewHost(bridge: bridge)
            .accessibilityLabel("Note")
            .onAppear {
                wire()
                // A new page renders on `ready`; one already up needs the note now.
                if bridge.isReady { render(reset: true) }
            }
            .onChange(of: source) { _, new in
                if let edit = session.inline {
                    if edit.owns(new) { return }   // the open edit's own keystroke
                    session.inline = nil           // Reload, another note, a save from elsewhere
                    bridge.send("endEdit")
                }
                render(reset: false)
            }
    }

    // MARK: - Rendering

    private func render(reset: Bool) {
        Self.sendRender(bridge, source: source, resolve: resolve, reset: reset, session: session)
    }

    // MARK: - Messages from the page

    private func wire() {
        let binding = $source
        let session = session
        let requestSave = onRequestSave
        let wiki = kbWikilinkHandler, tag = kbTagHandler, openNote = openKBNote
        bridge.onExternalLink = { url in
            if LinkSchemePolicy.allowsExternalOpen(url) { NSWorkspace.shared.open(url) }
        }
        bridge.onMessage = { [weak bridge = self.bridge] type, body in
            let changes: Set<String> = ["edit", "editDone", "editDiscard", "comment", "retract", "toggleTask"]
            if changes.contains(type), (body["v"] as? NSNumber)?.intValue != session.version { return }
            switch type {
            case "ready":
                // A fresh (or reloaded) page: give it the note. Any edit the
                // old page had open is gone with it.
                session.rendered = nil
                Self.sendRender(bridge, source: binding.wrappedValue, resolve: resolve, reset: true, session: session)
            case "openWiki":
                guard let target = body["target"] as? String else { return }
                _ = InlineMarkdownText.openWikilink(target: target, kbHandler: wiki, openKBNote: openNote)
            case "openTag":
                if let t = body["tag"] as? String { tag?(t) }
            case "openURL":
                guard let s = body["url"] as? String, let url = URL(string: s),
                      LinkSchemePolicy.allowsExternalOpen(url) else { return }
                NSWorkspace.shared.open(url)
            case "edit":
                Self.applyEdit(body, source: binding, session: session)
            case "editDone":
                Self.sendRender(bridge, source: binding.wrappedValue, resolve: resolve, reset: false, session: session, force: true)
            case "editDiscard":
                if let edit = session.inline {
                    session.inline = nil
                    binding.wrappedValue = edit.snapshot
                }
                Self.sendRender(bridge, source: binding.wrappedValue, resolve: resolve, reset: false, session: session, force: true)
            case "comment":
                guard session.inline == nil,
                      let text = body["text"] as? String,
                      KBInlineEdit.canSendComment(text, inlineEditOpen: false),
                      let after = (body["afterLine"] as? NSNumber)?.intValue,
                      let updated = KBReaderDocument.insertComment(
                        in: binding.wrappedValue, afterLine: after,
                        quote: body["quote"] as? String, comment: text)
                else { return }
                binding.wrappedValue = updated
                requestSave()
            case "retract":
                guard session.inline == nil,
                      let marker = body["marker"] as? String,
                      let from = (body["from"] as? NSNumber)?.intValue,
                      let to = (body["to"] as? NSNumber)?.intValue,
                      let updated = KBReaderDocument.removeMarker(in: binding.wrappedValue, marker: marker, from: from, to: to)
                else { return }
                binding.wrappedValue = updated
                requestSave()
            case "toggleTask":
                guard session.inline == nil,
                      let line = (body["line"] as? NSNumber)?.intValue,
                      let updated = KBReaderDocument.toggleTask(in: binding.wrappedValue, line: line)
                else { return }
                binding.wrappedValue = updated
            default:
                break
            }
        }
    }

    private static func sendRender(_ bridge: KBWebBridge?, source: String, resolve: (String) -> String?,
                                   reset: Bool, session: Session, force: Bool = false) {
        guard let bridge, force || reset || session.rendered != source else { return }
        session.rendered = source
        // A render that isn't the open edit's own ends that edit.
        session.inline = nil
        session.version += 1
        bridge.send("render", [
            "v": session.version,
            "source": source,
            "display": KBReaderDocument.displayMarkdown(source, linkify: InlineMarkdownText.linkifiedMarkdown),
            "missing": KBReaderDocument.missingTargets(in: source, resolve: resolve),
            "reset": reset,
        ])
    }

    /// One keystroke of an in-place edit. The first opens the edit against
    /// the current draft (checking the page's idea of the block still matches
    /// it); every one applies the whole buffer to that snapshot.
    private static func applyEdit(_ body: [String: Any], source: Binding<String>, session: Session) {
        guard let buffer = body["buffer"] as? String, let original = body["original"] as? String else { return }
        let target: KBInlineEdit.Target
        switch body["kind"] as? String {
        case "block":
            guard let s = (body["start"] as? NSNumber)?.intValue, let e = (body["end"] as? NSNumber)?.intValue else { return }
            target = .block(lineStart: s, lineEnd: e)
        case "cell":
            guard let l = (body["line"] as? NSNumber)?.intValue, let c = (body["col"] as? NSNumber)?.intValue else { return }
            target = .cell(sourceLine: l, col: c)
        default:
            return
        }
        if session.inline?.target != target {
            let current = source.wrappedValue
            // The page edits what it rendered; refuse if the draft moved on.
            switch target {
            case .block(let s, let e):
                guard KBReaderDocument.lines(current, s, e) == original else { return }
            case .cell(let l, let c):
                guard KBReaderDocument.cell(current, line: l, col: c) == original else { return }
            }
            session.inline = KBInlineEdit(target: target, snapshot: current, original: original, buffer: original)
        }
        session.inline?.buffer = buffer
        if let edit = session.inline {
            session.rendered = edit.applied
            source.wrappedValue = edit.applied
        }
    }
}
