import SwiftUI
import AppKit
import Combine

/// The center pane: views and edits a single knowledge-base file. Source editing
/// for `.md`/`.yaml`, a rendered preview for markdown. Saves go through
/// `KnowledgeBaseFileWriter` (atomic write + git commit) with a baseline-mtime
/// conflict guard so a concurrent scout-plugin write is surfaced, not clobbered.
struct KBEditorView: View {
    let node: KBNode
    @ObservedObject var service: KnowledgeBaseService
    let writer: KnowledgeBaseFileWriter
    /// Called after the open file is deleted — parent clears selection.
    let onDeleted: () -> Void
    /// Called after a rename — parent re-selects the file at its new URL.
    let onRenamed: (URL) -> Void
    /// Called when a rename moved the file but couldn't commit it. The parent
    /// shows it: reopening the note at its new path replaces this view, so an
    /// alert here would never appear.
    let onRenameProblem: (String) -> Void
    /// Called when the user clicks the breadcrumb root — parent clears the
    /// selection, returning to the overview (stats + global graph).
    let onOverview: () -> Void
    /// Called when an undo or redo of a rename moves a note from the first
    /// URL to the second, so the parent can follow it if it's still showing
    /// the note.
    var onMoved: ((URL, URL) -> Void)? = nil

    @EnvironmentObject private var vaultUndo: VaultUndoController
    @Environment(\.undoManager) private var undoManager

    private enum Mode: String { case read, rich, source }

    @State private var draft: String = ""
    @State private var originalText: String = ""
    /// Text on disk when the file was loaded — the save conflict guard compares
    /// content, not mtime, so coarse filesystem timestamps can't hide a change.
    /// nil = the file was missing/unreadable at load.
    @State private var baselineContents: String? = nil
    /// Disk mtime at load — a cheap trigger for external-change detection (the
    /// content compare confirms before flagging).
    @State private var baselineDate: Date? = nil
    // Markdown opens in "Read": rendered, but you edit in place (double-click a
    // block/cell). Rich (live markdown) and Source (raw) are one toggle away.
    // loadFile() forces Source for non-markdown (YAML).
    @State private var mode: Mode = .read
    @State private var isSaving = false
    @State private var errorMessage: String? = nil
    @State private var showConflict = false
    /// File changed on disk while the user had unsaved edits.
    @State private var externallyChanged = false
    @State private var showRename = false
    @State private var renameText = ""
    @State private var showDeleteConfirm = false
    /// Reload was asked for with unsaved edits: confirm before discarding.
    @State private var showReloadConfirm = false
    /// The file exists but couldn't be read. The editor shows an error with
    /// Retry instead of an empty document, so nothing typed into a blank
    /// stand-in can be saved over the real file.
    @State private var isUnreadable = false

    private var isDirty: Bool { draft != originalText }

    /// What the conflict alert offers. "Keep my edits" has the cancel role, so
    /// Esc (or closing the alert) never discards work.
    enum ConflictChoice: CaseIterable {
        case overwrite, keepEditing, reload

        var title: String {
            switch self {
            case .overwrite: return "Overwrite"
            case .keepEditing: return "Keep my edits"
            case .reload: return "Discard my edits and reload"
            }
        }

        var role: ButtonRole? {
            switch self {
            case .overwrite, .reload: return .destructive
            case .keepEditing: return .cancel
            }
        }
    }

    /// What a Reload button does. With unsaved edits it asks first, like the
    /// conflict alert and Quit: an edit is never discarded without asking (C1).
    enum ReloadStep: Equatable {
        case reloadNow, confirmDiscard

        static func forReload(isDirty: Bool) -> ReloadStep { isDirty ? .confirmDiscard : .reloadNow }
    }

    /// What every Reload button runs (`requestReload()`): reload at once with
    /// nothing to lose, else only ask. Static so tests drive the same code.
    static func requestReload(isDirty: Bool, reload: () -> Void, askToDiscard: () -> Void) {
        switch ReloadStep.forReload(isDirty: isDirty) {
        case .reloadNow: reload()
        case .confirmDiscard: askToDiscard()
        }
    }

    /// The Reload confirmation's buttons. "Keep Editing" has the cancel role,
    /// so Esc (or closing the alert) never discards work.
    enum ReloadChoice: CaseIterable {
        case keepEditing, discardAndReload

        var title: String {
            switch self {
            case .keepEditing: return "Keep Editing"
            case .discardAndReload: return "Discard Edits and Reload"
            }
        }

        var role: ButtonRole? {
            switch self {
            case .keepEditing: return .cancel
            case .discardAndReload: return .destructive
            }
        }
    }

    /// What the editor opens with. Pure, so tests can pin the rules.
    enum LoadResult: Equatable {
        case editing(KBDraft, restored: Bool)
        case unreadable
    }

    /// An unsaved draft for this file wins over disk (it keeps the baseline it
    /// was typed against, so saving still detects an external change). A file
    /// that exists but can't be read is never shown as an empty document.
    static func resolveLoad(diskText: String?, fileExists: Bool, diskDate: Date?,
                            stashed: KBDraft?) -> LoadResult {
        if let stashed, stashed.isDirty { return .editing(stashed, restored: true) }
        if diskText == nil && fileExists { return .unreadable }
        return .editing(.fromDisk(diskText, date: diskDate), restored: false)
    }

    private var currentDraft: KBDraft {
        KBDraft(text: draft, originalText: originalText,
                baselineContents: baselineContents, baselineDate: baselineDate)
    }
    private var isMarkdown: Bool { node.ext == "md" }

    var body: some View {
        VStack(spacing: 0) {
            header
            EditorialRule()
            if externallyChanged { changedOnDiskBanner }
            content
        }
        .background(Color.clear)
        .onChange(of: node.id) { _, _ in openFile() }
        .onAppear { openFile() }
        // Mirror every change into the app-lifetime draft store, so leaving
        // this note (another note, a link, the overview, another page) keeps
        // unsaved edits; coming back restores them.
        .onChange(of: currentDraft) { _, new in
            guard !isUnreadable else { return }
            service.drafts.update(new, for: node.url)
        }
        // Service reparses on FSEvents; re-check whether our open file moved
        // out from under us.
        .onReceive(service.objectWillChange) { _ in
            DispatchQueue.main.async { detectExternalChange() }
        }
        .alert("File changed on disk", isPresented: $showConflict) {
            ForEach(ConflictChoice.allCases, id: \.self) { choice in
                Button(choice.title, role: choice.role) { resolveConflict(choice) }
            }
        } message: {
            Text("\(node.name) was modified by another process since you opened it. Overwrite it with your version, keep editing, or discard your changes and reload?")
        }
        .alert("Couldn't save", isPresented: Binding(
            get: { errorMessage != nil },
            set: { if !$0 { errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
        .alert("Discard your edits to \(node.displayName)?", isPresented: $showReloadConfirm) {
            ForEach(ReloadChoice.allCases, id: \.self) { choice in
                Button(choice.title, role: choice.role) {
                    if choice == .discardAndReload { loadFile() }
                }
            }
        } message: {
            Text("Reloading shows the file as it is on disk and throws away the changes you haven't saved.")
        }
        .alert("Delete \(node.displayName)?", isPresented: $showDeleteConfirm) {
            Button("Delete", role: .destructive) { performDelete() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This removes \(node.relativePath) and commits the deletion. You can undo it with Edit ▸ Undo.")
        }
        .sheet(isPresented: $showRename) { renameSheet }
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                breadcrumb
                HStack(spacing: 6) {
                    Text(node.name).font(DS.mono(11)).foregroundStyle(DS.Ink.p3)
                    if isDirty {
                        Circle().fill(DS.Accent.fill).frame(width: 6, height: 6)
                        Text("unsaved").font(DS.sans(10.5)).foregroundStyle(DS.Accent.ink)
                    }
                }
            }
            Spacer()

            if isMarkdown {
                EditorialSegmentedControl(
                    selection: Binding(get: { mode },
                                       set: { mode = $0 }),
                    options: [(label: "Read", value: .read),
                              (label: "Rich", value: .rich),
                              (label: "Source", value: .source)],
                    minSegmentWidth: 54
                )
            }

            Button { requestReload() } label: { Label("Reload", systemImage: "arrow.clockwise") }
                .buttonStyle(.plain).foregroundStyle(DS.Ink.p3).font(DS.sans(12))
                .help(isDirty ? "Reload from disk (asks before discarding your unsaved edits)" : "Reload from disk")

            Button(action: save) {
                Label("Save", systemImage: "checkmark")
                    .font(DS.sans(12, weight: .semibold))
            }
            .buttonStyle(.plain)
            .foregroundStyle(isDirty && !isSaving ? DS.Accent.onFill : DS.Ink.p4)
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(Capsule().fill(isDirty && !isSaving ? DS.Accent.fill : DS.Paper.sunk))
            .disabled(!isDirty || isSaving || isUnreadable)
            .keyboardShortcut("s", modifiers: .command)

            Menu {
                Button("Rename…") { renameText = node.displayName; showRename = true }
                Button("Reveal in Finder") { NSWorkspace.shared.activateFileViewerSelecting([node.url]) }
                Divider()
                Button("Delete…", role: .destructive) { showDeleteConfirm = true }
            } label: {
                Image(systemName: "ellipsis.circle").font(DS.sans(15)).foregroundStyle(DS.Ink.p3)
            }
            .menuStyle(.borderlessButton).frame(width: 28)
            .accessibilityLabel("Note actions")
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
    }

    private var breadcrumb: some View {
        let parts = node.relativePath.components(separatedBy: "/")
        return HStack(spacing: 4) {
            ForEach(Array(parts.enumerated()), id: \.offset) { i, part in
                if i > 0 { Text("/").font(DS.sans(11)).foregroundStyle(DS.Ink.p4) }
                if i == 0 {
                    // Root crumb navigates back to the overview.
                    Button(action: onOverview) {
                        Text(part)
                            .font(DS.sans(11))
                            .foregroundStyle(DS.Accent.ink)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .help("Back to overview")
                } else {
                    Text((part as NSString).deletingPathExtension.isEmpty ? part : (i == parts.count - 1 ? (part as NSString).deletingPathExtension : part))
                        .font(DS.sans(11, weight: i == parts.count - 1 ? .semibold : .regular))
                        .foregroundStyle(i == parts.count - 1 ? DS.Ink.p1 : DS.Ink.p3)
                }
            }
        }
    }

    private var changedOnDiskBanner: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(DS.Status.warn)
            Text("This file changed on disk. Reload to see the new version, or save to overwrite it.")
                .font(DS.sans(12)).foregroundStyle(DS.Ink.p2)
            Spacer()
            Button("Reload") { requestReload() }.buttonStyle(.plain)
                .font(DS.sans(12, weight: .semibold)).foregroundStyle(DS.Accent.ink)
        }
        .padding(.horizontal, 20).padding(.vertical, 8)
        .background(DS.Accent.wash)
    }

    // MARK: - Content

    private var unreadableState: some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "exclamationmark.triangle")
                .font(DS.sans(28)).foregroundStyle(DS.Status.warn)
            Text("Couldn't read \(node.name)")
                .font(DS.serif(17, weight: .medium)).foregroundStyle(DS.Ink.p2)
            Text("It may not be UTF-8 text, or it may be unavailable right now. Scout won't edit it until it can read it.")
                .font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
                .multilineTextAlignment(.center).frame(maxWidth: 380)
            Button("Retry") { loadFile() }.buttonStyle(.borderedProminent)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private var content: some View {
        if isUnreadable {
            unreadableState
        } else if !isMarkdown {
            KBSourceEditor(text: $draft)
        } else {
            switch mode {
            case .read:
                // Rendered, but editable in place: double-click a paragraph,
                // heading, list item or table cell to edit just that piece.
                // Select any text to leave Scout a comment about it; a
                // comment inserts a marker and saves immediately.
                KBReaderWebView(source: $draft, resolve: { [service] in service.resolveWikilink($0) },
                                onRequestSave: { save() })
            case .rich:
                KBLiveEditor(text: $draft)
            case .source:
                KBSourceEditor(text: $draft)
            }
        }
    }

    // MARK: - Actions

    /// Open the file, restoring an unsaved draft if one is held for it.
    private func openFile() {
        apply(Self.resolveLoad(
            diskText: service.readFile(node.url),
            fileExists: FileManager.default.fileExists(atPath: node.url.path),
            diskDate: GuardedFileWrite.fsModificationDate(node.url),
            stashed: service.drafts.draft(for: node.url)))
    }

    /// A Reload button: reloads at once with nothing to lose, else asks.
    private func requestReload() {
        Self.requestReload(isDirty: isDirty, reload: { loadFile() }, askToDiscard: { showReloadConfirm = true })
    }

    /// Load from disk, discarding any unsaved draft. A Reload button reaches
    /// it through `requestReload()`, which asks first when there are edits.
    private func loadFile() {
        service.drafts.discard(for: node.url)
        apply(Self.resolveLoad(
            diskText: service.readFile(node.url),
            fileExists: FileManager.default.fileExists(atPath: node.url.path),
            diskDate: GuardedFileWrite.fsModificationDate(node.url),
            stashed: nil))
    }

    private func apply(_ result: LoadResult) {
        externallyChanged = false
        if !isMarkdown { mode = .source }
        switch result {
        case .unreadable:
            isUnreadable = true
            draft = ""
            originalText = ""
            baselineContents = nil
            baselineDate = GuardedFileWrite.fsModificationDate(node.url)
        case .editing(let loaded, let restored):
            isUnreadable = false
            draft = loaded.text
            originalText = loaded.originalText
            baselineContents = loaded.baselineContents
            baselineDate = loaded.baselineDate
            // A restored draft may predate a change on disk: flag it now.
            if restored { detectExternalChange() }
        }
    }

    private func resolveConflict(_ choice: ConflictChoice) {
        switch choice {
        case .overwrite: forceSave()
        // Edits stay; the banner keeps the conflict visible until resolved.
        case .keepEditing: externallyChanged = true
        case .reload: loadFile()
        }
    }

    /// Detect an external change to the open file: the mtime is the cheap
    /// trigger, a content compare confirms (so the app's own atomic save or a
    /// same-content touch doesn't flag). Silently reloads if the user has no
    /// unsaved edits; otherwise flags the banner.
    private func detectExternalChange() {
        guard !isSaving else { return }
        // Can't recover from unreadable by comparing contents: just retry.
        if isUnreadable {
            if service.readFile(node.url) != nil { loadFile() }
            return
        }
        guard FileManager.default.fileExists(atPath: node.url.path) else { return }
        let current = GuardedFileWrite.fsModificationDate(node.url)
        guard let current, current != baselineDate else { return }
        let diskText = service.readFile(node.url)
        guard diskText != baselineContents else {
            baselineDate = current   // content unchanged — just track the new mtime
            return
        }
        if isDirty {
            externallyChanged = true
        } else {
            loadFile()
        }
    }

    private func save() {
        guard isDirty, !isSaving, !isUnreadable else { return }
        performSave(baseline: baselineContents)
    }

    /// Overwrite despite a detected conflict — re-baseline to what's on disk
    /// right now so the guard passes, then save.
    private func forceSave() {
        performSave(baseline: service.readFile(node.url))
    }

    private func performSave(baseline captured: String?) {
        isSaving = true
        let contents = draft
        Task {
            do {
                let change = try await writer.save(fileURL: node.url, contents: contents,
                                                   baselineContents: captured, label: node.displayName)
                await MainActor.run {
                    markSaved(contents)
                    registerUndo(change)
                }
            } catch KBWriterError.conflict {
                await MainActor.run { isSaving = false; showConflict = true }
            } catch {
                await MainActor.run {
                    // A commit failure means the file itself was written —
                    // reflect the saved state, then surface the git problem.
                    if case KBWriterError.commitFailed = error {
                        markSaved(contents)
                    } else {
                        isSaving = false
                    }
                    errorMessage = WriteProblem.from(error, action: "change the note").message
                }
            }
        }
    }

    private func registerUndo(_ change: VaultChange?) {
        guard let change else { return }
        let writer = writer
        vaultUndo.register(
            .restoring(change) { change, direction in try await writer.restore(change, direction) },
            with: undoManager
        ) { [service] in service.reload() }
    }

    /// Undo of a rename moves the note back; its unsaved draft and the
    /// selection follow it, so a dirty draft can't be saved to the old name
    /// and recreate it. Redo moves both forward again.
    private func registerRenameUndo(_ change: VaultChange?, from original: URL, to dest: URL) {
        guard let change else { return }
        vaultUndo.register(
            Self.renameUndoStep(change, from: original, to: dest, writer: writer,
                                drafts: service.drafts, onMoved: onMoved),
            with: undoManager
        ) { [service] in service.reload() }
    }

    /// The undo step for a rename: the file, its draft and (through
    /// `onMoved`) the selection move back on undo and forward on redo.
    static func renameUndoStep(
        _ change: VaultChange, from original: URL, to dest: URL,
        writer: KnowledgeBaseFileWriter, drafts: KBDraftStore, onMoved: ((URL, URL) -> Void)?
    ) -> UndoStep {
        UndoStep(
            actionName: change.actionName,
            undo: {
                try await writer.restore(change, .undo)
                drafts.move(from: dest, to: original)
                onMoved?(dest, original)
            },
            redo: {
                try await writer.restore(change, .redo)
                drafts.move(from: original, to: dest)
                onMoved?(original, dest)
            })
    }

    /// The user deleted the file on purpose: drop its draft, and stop the
    /// draft mirror from re-recording it as this view goes away.
    private func forgetDraft() {
        originalText = draft
        service.drafts.discard(for: node.url)
    }

    private func markSaved(_ contents: String) {
        originalText = contents
        baselineContents = contents
        baselineDate = GuardedFileWrite.fsModificationDate(node.url)
        externallyChanged = false
        isSaving = false
    }

    private func performDelete() {
        Task {
            do {
                let change = try await writer.delete(fileURL: node.url, label: node.displayName)
                await MainActor.run { forgetDraft(); service.reload(); onDeleted(); registerUndo(change) }
            } catch {
                await MainActor.run {
                    // The removal itself succeeded when only the commit failed.
                    if case KBWriterError.commitFailed = error { forgetDraft(); service.reload(); onDeleted() }
                    errorMessage = WriteProblem.from(error, action: "change the note").message
                }
            }
        }
    }

    private func performRename() {
        let newName = renameText
        let source = node.url
        let planned = try? KnowledgeBaseFileWriter.renameDestination(for: source, to: newName)
        Task {
            do {
                let (dest, change) = try await writer.renameReturningChange(fileURL: source, to: newName)
                await MainActor.run {
                    finishRename(from: source, to: dest)
                    // Undo only when the rename fully succeeded: a rename
                    // whose commit failed threw before its change was
                    // captured, so it has nothing safe to restore.
                    registerRenameUndo(change, from: source, to: dest)
                }
            } catch {
                await MainActor.run {
                    switch Self.renameOutcome(after: error, planned: planned) {
                    case .movedButUncommitted(let moved, let problem):
                        finishRename(from: source, to: moved)
                        onRenameProblem(problem)
                    case .notMoved(let problem):
                        errorMessage = problem
                        showRename = false
                    }
                }
            }
        }
    }

    /// What a rename that threw left behind. Only a failed commit means the
    /// move itself happened; its message then goes to the parent, because
    /// this view is replaced when the note reopens at its new path.
    enum RenameOutcome: Equatable {
        case movedButUncommitted(URL, problem: String)
        case notMoved(problem: String)
    }

    static func renameOutcome(after error: Error, planned: URL?) -> RenameOutcome {
        let problem = KBWriterError.message(for: error)
        if case KBWriterError.commitFailed = error, let planned {
            return .movedButUncommitted(planned, problem: problem)
        }
        return .notMoved(problem: problem)
    }

    /// The file now lives at `dest`: carry its unsaved draft over and reopen it
    /// there, so the edits and the quit prompt follow the file.
    private func finishRename(from old: URL, to dest: URL) {
        service.drafts.move(from: old, to: dest)
        service.reload(); onRenamed(dest); showRename = false
    }

    private var renameSheet: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Rename note").font(DS.serif(16, weight: .semibold)).foregroundStyle(DS.Ink.p1)
            TextField("New name", text: $renameText)
                .textFieldStyle(.roundedBorder).font(DS.sans(13))
                .onSubmit(performRename)
            HStack {
                Spacer()
                Button("Cancel") { showRename = false }.keyboardShortcut(.cancelAction)
                Button("Rename") { performRename() }.keyboardShortcut(.defaultAction)
                    .disabled(renameText.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20).frame(width: 360)
    }

}

/// Plain-text source editor with a monospaced font over the recessed paper
/// surface. Wraps `TextEditor` so the KB editor reads markdown/YAML source the
/// way the plugin writes it.
struct KBSourceEditor: View {
    @Binding var text: String

    var body: some View {
        TextEditor(text: $text)
            .font(DS.mono(12.5))
            .foregroundStyle(DS.Ink.p1)
            .scrollContentBackground(.hidden)
            .background(DS.Paper.sunk)
            .padding(.horizontal, 12).padding(.vertical, 10)
    }
}
