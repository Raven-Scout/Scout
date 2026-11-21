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
    /// Called after a rename that left some linking notes alone (unsaved edits
    /// in them, or they changed meanwhile), with a title and message. Shown by
    /// the parent for the same reason as `onRenameProblem`.
    var onRenameNotice: ((_ title: String, _ message: String) -> Void)? = nil
    /// Called when the user clicks the breadcrumb root — parent clears the
    /// selection, returning to the overview (stats + global graph).
    let onOverview: () -> Void
    /// Called when an undo or redo of a rename moves a note from the first
    /// URL to the second, so the parent can follow it if it's still showing
    /// the note.
    var onMoved: ((URL, URL) -> Void)? = nil
    /// Whether the parent shows the Links panel beside this note, and the
    /// header button that shows or hides it (nil: no button).
    var linksPanelShown: Bool = true
    var onToggleLinksPanel: (() -> Void)? = nil

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
    /// The note opened on an unsaved draft kept from earlier: a banner says
    /// so, in every mode, until it's saved, discarded or dismissed (UI audit T1).
    @State private var restoredDraft = false
    @State private var draftBannerDismissed = false
    @State private var showRename = false
    @State private var renameText = ""
    /// Point the notes that link here at the new name (UI audit #75).
    @State private var renameUpdatesLinks = true
    /// Why the last rename didn't happen, shown in the sheet so the typed
    /// name isn't lost.
    @State private var renameError: String? = nil
    @State private var isRenaming = false
    /// The notes linking here and the note's links to itself, read once when
    /// the sheet opens rather than on every keystroke.
    @State private var renameInbound: [KBLinkRewrite.Inbound] = []
    @State private var renameSelfLinks = 0
    @State private var showDeleteConfirm = false
    /// Reload was asked for with unsaved edits: confirm before discarding.
    @State private var showReloadConfirm = false
    /// The file exists but couldn't be read. The editor shows an error with
    /// Retry instead of an empty document, so nothing typed into a blank
    /// stand-in can be saved over the real file.
    @State private var isUnreadable = false
    /// ⌘F in the open note, whichever mode it's in (UI audit #77).
    @StateObject private var find = KBFindController()

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
    /// Read mode's web page is on screen (the others are text views).
    private var showsReader: Bool { isMarkdown && !isUnreadable && mode == .read }

    var body: some View {
        // The column never asks for more height than it's offered: measured
        // for the split view's minimum size (a near-zero width), a banner
        // wrapped a word per line and asked for 1500-2300 pt, which pushed
        // the whole window's content up under the title bar.
        KBNoteColumnLayout {
            VStack(spacing: 0) {
                header
                EditorialRule()
                if externallyChanged {
                    changedOnDiskBanner
                } else if Self.showsDraftBanner(restored: restoredDraft, isDirty: isDirty,
                                                dismissed: draftBannerDismissed) {
                    draftBanner
                }
                if find.readerBarVisible && showsReader { KBReaderFindBar(controller: find) }
                content
            }
        }
        .background {
            Button("Find in Note") { find.show(inReader: showsReader) }
                .keyboardShortcut(AppShortcut.find)
                .disabled(isUnreadable)
                .frame(width: 0, height: 0)
                .opacity(0)
                .accessibilityHidden(true)
        }
        .background(Color.clear)
        .onChange(of: node.id) { _, _ in
            find.readerBarVisible = false   // a find bar belongs to the note it was opened on
            openFile()
        }
        .onChange(of: mode) { _, _ in find.readerBarVisible = false }
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
        // Coming back to the app is when another editor's change shows up;
        // check then too, not only when the watcher's reparse lands.
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            detectExternalChange()
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

    /// The header: the note's path and name beside its controls when there's
    /// room for both, else the controls on a row of their own above the path.
    /// The path truncates in the middle and every control keeps its size, so
    /// a narrow reader (sidebar, tree and Links panel open at 1280 pt) never
    /// wraps a word a letter per line (UI audit T1).
    private var header: some View {
        let minTitleWidth = Self.minTitleWidth
        return ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) {
                titleColumn
                    .frame(minWidth: minTitleWidth, idealWidth: minTitleWidth,
                           maxWidth: .infinity, alignment: .leading)
                controls(compact: false)
            }
            stacked(compact: false)
            // Narrower still: the mode switch becomes a menu.
            stacked(compact: true)
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
    }

    private func stacked(compact: Bool) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Spacer(minLength: 0)
                controls(compact: compact)
            }
            titleColumn.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private static let modeOptions: [(label: String, value: Mode)] =
        [(label: "Read", value: .read), (label: "Rich", value: .rich), (label: "Source", value: .source)]

    /// The narrowest the path and name get beside the controls before the
    /// controls move to their own row, at the Standard text size. At 160 pt
    /// the root crumb took half of it and the note's name was cut to about 8
    /// characters.
    nonisolated static let minTitleDesignWidth: CGFloat = 280

    /// The narrowest the folders crumb gets at the Standard text size: room
    /// for an ellipsis and a letter either side, never a clipped glyph.
    nonisolated static let minFolderCrumbDesignWidth: CGFloat = 32

    /// `minTitleDesignWidth` at a text-size `factor`. The crumbs and the
    /// name grow with Settings ▸ General ▸ Text size, so a fixed floor held
    /// fewer letters at every step up: at Largest the folders shrank to a
    /// bare "…" and the name to about 9 characters before the controls moved.
    nonisolated static func minTitleWidth(factor: CGFloat) -> CGFloat {
        DS.scaledSize(minTitleDesignWidth, factor: factor)
    }

    nonisolated static func minFolderCrumbWidth(factor: CGFloat) -> CGFloat {
        DS.scaledSize(minFolderCrumbDesignWidth, factor: factor)
    }

    /// The floors at the user's text size. Read in `body`, so changing the
    /// setting re-lays out the header.
    static var minTitleWidth: CGFloat { minTitleWidth(factor: TextScale.shared.factor) }
    static var minFolderCrumbWidth: CGFloat { minFolderCrumbWidth(factor: TextScale.shared.factor) }

    private var titleColumn: some View {
        VStack(alignment: .leading, spacing: 2) {
            breadcrumb
            HStack(spacing: 6) {
                Text(node.name).font(DS.font(.caption, .mono)).foregroundStyle(DS.Ink.p3)
                    .lineLimit(1).truncationMode(.middle)
                if isDirty {
                    Circle().fill(DS.Accent.fill).frame(width: 6, height: 6)
                    Text("unsaved").font(DS.font(.caption2)).foregroundStyle(DS.Accent.ink)
                        .lineLimit(1).fixedSize()
                }
            }
        }
    }

    private func controls(compact: Bool) -> some View {
        HStack(spacing: 8) {
            if isMarkdown {
                if compact {
                    Picker("View", selection: Binding(get: { mode }, set: { mode = $0 })) {
                        ForEach(Self.modeOptions, id: \.value) { Text($0.label).tag($0.value) }
                    }
                    .pickerStyle(.menu).labelsHidden()
                    .font(DS.font(.label))
                    .fixedSize()
                    .accessibilityLabel("View")
                } else {
                    EditorialSegmentedControl(
                        selection: Binding(get: { mode },
                                           set: { mode = $0 }),
                        options: Self.modeOptions,
                        minSegmentWidth: 54
                    )
                    .fixedSize()
                }
            }

            Button { requestReload() } label: {
                Label("Reload", systemImage: "arrow.clockwise").labelStyle(.iconOnly)
            }
            .buttonStyle(.dsQuiet)
            .fixedSize()
            .help(isDirty ? "Reload from disk (asks before discarding your unsaved edits)" : "Reload from disk")
            .accessibilityLabel("Reload from disk")

            Button(action: save) {
                Label("Save", systemImage: "checkmark").lineLimit(1)
            }
            .buttonStyle(.dsPrimary)
            .fixedSize()
            .disabled(!isDirty || isSaving || isUnreadable)
            .keyboardShortcut("s", modifiers: .command)

            if let onToggleLinksPanel {
                Button(action: onToggleLinksPanel) {
                    Image(systemName: "sidebar.right").font(DS.font(.label))
                        .foregroundStyle(linksPanelShown ? DS.Ink.p1 : DS.Ink.p3)
                }
                .buttonStyle(.plainHit) // ds-guard: hit area (an icon button)
                .frame(width: 24, height: 24).contentShape(Rectangle())
                .help(linksPanelShown ? "Hide the Links panel" : "Show the Links panel")
                .accessibilityLabel(linksPanelShown ? "Hide Links panel" : "Show Links panel")
            }

            Menu {
                Button("Rename…") {
                    renameText = node.displayName
                    renameUpdatesLinks = true
                    renameError = nil
                    renameInbound = KBLinkRewrite.inboundNotes(to: node.relativePath, in: service.index)
                    renameSelfLinks = KBLinkRewrite.selfLinks(of: node.relativePath, in: service.index)
                    showRename = true
                }
                Button("Reveal in Finder") { NSWorkspace.shared.activateFileViewerSelecting([node.url]) }
                Divider()
                Button("Delete…", role: .destructive) { showDeleteConfirm = true }
            } label: {
                Image(systemName: "ellipsis.circle").font(DS.font(.headline)).foregroundStyle(DS.Ink.p3)
            }
            .menuStyle(.borderlessButton).frame(width: 28)
            .accessibilityLabel("Note actions")
        }
        .fixedSize()
    }

    /// The crumbs after the root: the folders between, and the note's stem.
    /// Pure, so tests pin it.
    static func breadcrumbTail(_ relativePath: String) -> (folders: String, note: String) {
        let parts = relativePath.components(separatedBy: "/").dropFirst()
        guard let last = parts.last else { return ("", "") }
        let stem = (last as NSString).deletingPathExtension
        return (parts.dropLast().joined(separator: " / "), stem.isEmpty ? last : stem)
    }

    /// Root crumb (back to the overview), then the folders and the note on
    /// one line each truncating in the middle: the folders give way first,
    /// then the note's name.
    private var breadcrumb: some View {
        let root = node.relativePath.components(separatedBy: "/").first ?? ""
        let tail = Self.breadcrumbTail(node.relativePath)
        return HStack(spacing: 4) {
            // Root crumb navigates back to the overview.
            Button(action: onOverview) {
                Text(root).lineLimit(1)
            }
            .buttonStyle(.ds(.link, size: .small))
            .fixedSize()
            .help("Back to overview")
            if !tail.folders.isEmpty {
                Text("/").font(DS.font(.caption)).foregroundStyle(DS.Ink.p4).fixedSize()
                KBCrumbFloorLayout(floor: Self.minFolderCrumbWidth) {
                    Text(tail.folders)
                        .font(DS.font(.caption)).foregroundStyle(DS.Ink.p3)
                        .lineLimit(1).truncationMode(.middle)
                }
                .layoutPriority(0)
            }
            if !tail.note.isEmpty {
                Text("/").font(DS.font(.caption)).foregroundStyle(DS.Ink.p4).fixedSize()
                Text(tail.note)
                    .font(DS.font(.caption, weight: .semibold)).foregroundStyle(DS.Ink.p1)
                    .lineLimit(1).truncationMode(.middle)
                    .layoutPriority(1)
            }
        }
        .help(node.relativePath)
    }

    /// Shown the moment the file changes under unsaved edits, with both ways
    /// out; neither discards anything without asking.
    private var changedOnDiskBanner: some View {
        Self.changedOnDiskBanner(name: node.displayName, saveMine: { forceSave() },
                                 reload: { requestReload() })
    }

    /// Static so the layout tests host the banner the reader shows.
    static func changedOnDiskBanner(name: String, saveMine: @escaping @MainActor () -> Void,
                                    reload: @escaping @MainActor () -> Void) -> some View {
        DSBanner(tone: .warning, title: "\(name) changed on disk",
                 message: "Something else changed this file while you were editing it. Your edits are still here. "
                    + "Save your version to replace the file, or reload to see the new one.",
                 actions: [ProblemAction("Save My Version", perform: saveMine),
                           ProblemAction("Reload…", perform: reload)])
            .padding(.horizontal, 20).padding(.vertical, 8)
    }

    /// Whether the "Unsaved draft" banner shows. Pure, so tests pin it.
    static func showsDraftBanner(restored: Bool, isDirty: Bool, dismissed: Bool) -> Bool {
        restored && isDirty && !dismissed
    }

    private var draftBanner: some View {
        Self.draftBanner(save: { save() }, discard: { requestReload() },
                         dismiss: { draftBannerDismissed = true })
    }

    /// Static so the layout tests host the banner the reader shows.
    static func draftBanner(save: @escaping @MainActor () -> Void, discard: @escaping @MainActor () -> Void,
                            dismiss: @escaping @MainActor () -> Void) -> some View {
        DSBanner(tone: .info, title: "Unsaved draft",
                 message: "You edited this note earlier and haven't saved it. This is your draft, not the file on disk.",
                 actions: [ProblemAction("Save", perform: save),
                           ProblemAction("Discard Edits…", perform: discard)],
                 onDismiss: dismiss)
            .padding(.horizontal, 20).padding(.vertical, 8)
    }

    // MARK: - Content

    private var unreadableState: some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "exclamationmark.triangle")
                .font(DS.symbol(28)).foregroundStyle(DS.Status.warn)
            Text("Couldn't read \(node.name)")
                .dsFont(.title, .serif, weight: .medium).foregroundStyle(DS.Ink.p2)
            Text("It may not be UTF-8 text, or it may be unavailable right now. Scout won't edit it until it can read it.")
                .font(DS.font(.label)).foregroundStyle(DS.Ink.p3)
                .multilineTextAlignment(.center).frame(maxWidth: 380)
            Button("Retry") { loadFile() }.buttonStyle(.dsPrimary)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private var content: some View {
        if isUnreadable {
            unreadableState
        } else if !isMarkdown {
            KBSourceEditor(text: $draft, find: find)
        } else {
            switch mode {
            case .read:
                // Rendered, but editable in place: double-click a paragraph,
                // heading, list item or table cell to edit just that piece.
                // Select any text to leave Scout a comment about it; a
                // comment inserts a marker and saves immediately.
                KBReaderWebView(source: $draft, resolve: { [service] in service.resolveWikilink($0) },
                                onRequestSave: { save() }, find: find)
            case .rich:
                KBLiveEditor(text: $draft, find: find)
            case .source:
                KBSourceEditor(text: $draft, find: find)
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
            restoredDraft = false
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
            restoredDraft = restored
            draftBannerDismissed = false
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
        let url = node.url
        switch Self.externalChange(isDirty: isDirty, baselineDate: baselineDate,
                                   currentDate: GuardedFileWrite.fsModificationDate(url),
                                   baselineContents: baselineContents,
                                   diskText: { service.readFile(url) }) {
        case .none: break
        case .sameContent(let date): baselineDate = date
        case .reload: loadFile()
        case .conflict: externallyChanged = true
        }
    }

    /// What a change on disk means for the open note: the mtime is the cheap
    /// trigger, the content compare confirms (the app's own atomic save or a
    /// same-content touch isn't a change). Pure, so tests pin it.
    enum ExternalChange: Equatable {
        case none
        /// The mtime moved but the text didn't: track the new mtime.
        case sameContent(Date)
        /// Changed, nothing unsaved: show the new text.
        case reload
        /// Changed under unsaved edits: say so at once.
        case conflict
    }

    static func externalChange(isDirty: Bool, baselineDate: Date?, currentDate: Date?,
                               baselineContents: String?, diskText: () -> String?) -> ExternalChange {
        guard let currentDate, currentDate != baselineDate else { return .none }
        guard diskText() != baselineContents else { return .sameContent(currentDate) }
        return isDirty ? .conflict : .reload
    }

    private func save() {
        guard isDirty, !isSaving, !isUnreadable else { return }
        performSave(baseline: baselineContents)
    }

    /// Overwrite despite a detected conflict — re-baseline to what's on disk
    /// right now so the guard passes, then save.
    private func forceSave() {
        // A second click mid-save would re-baseline against the file this
        // save is writing and report a false change on disk.
        guard !isSaving else { return }
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
        // Saved: new edits are new, not the draft from before.
        restoredDraft = false
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
        guard !isRenaming, !renameText.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        let newName = renameText
        let source = node.url
        let planned = try? KnowledgeBaseFileWriter.renameDestination(for: source, to: newName)
        let plan: KBRenameLinkPlan? = renameUpdatesLinks
            ? KBRenameLinkPlan.make(renaming: node.relativePath, inbound: renameInbound, index: service.index,
                                    scoutDirectory: service.scoutDirectory, drafts: service.drafts)
            : nil
        isRenaming = true
        renameError = nil
        Task {
            do {
                let result = try await writer.renameUpdatingLinks(fileURL: source, to: newName, links: plan)
                await MainActor.run {
                    isRenaming = false
                    finishRename(from: source, to: result.url)
                    // The undo step covers the move and every rewritten
                    // note, and is registered even when the commit failed:
                    // the files changed either way.
                    registerRenameUndo(result.change, from: source, to: result.url)
                    if let notice = RenameLinks.outcomeNotice(result.links, commitProblem: result.commitProblem) {
                        if let onRenameNotice {
                            onRenameNotice(notice.title, notice.message)
                        } else if let problem = result.commitProblem {
                            onRenameProblem(problem)
                        }
                    }
                }
            } catch {
                await MainActor.run {
                    isRenaming = false
                    // `renameUpdatingLinks` reports a failed commit in its
                    // result; a thrown commit failure is kept for safety.
                    switch Self.renameOutcome(after: error, planned: planned) {
                    case .movedButUncommitted(let moved, let problem):
                        finishRename(from: source, to: moved)
                        onRenameProblem(problem)
                    case .notMoved(let problem):
                        // Stay in the sheet with the typed name, so it can
                        // be corrected and tried again.
                        renameError = problem
                    }
                }
            }
        }
    }

    /// What the rename sheet and its result say about the notes that link to
    /// the note being renamed (UI audit #75). Pure, so tests pin the wording.
    struct RenameLinks: Equatable {
        /// Links the rename rewrites, and the notes holding them (the note
        /// itself counts when it links to itself by name).
        let links: Int
        let notes: Int
        /// Of those notes, how many have unsaved edits (left alone).
        let unsaved: Int
        /// Bare links left alone because more than one note has the old name.
        var ambiguous: Int = 0

        static func make(inbound: [KBLinkRewrite.Inbound], selfLinks: Int = 0,
                         unsavedPaths: Set<String>) -> RenameLinks {
            let linking = inbound.filter { $0.links > 0 }
            return RenameLinks(
                links: linking.reduce(0) { $0 + $1.links } + selfLinks,
                notes: linking.count + (selfLinks > 0 ? 1 : 0),
                unsaved: linking.filter { unsavedPaths.contains($0.path) }.count,
                ambiguous: inbound.reduce(0) { $0 + $1.ambiguous })
        }

        private static func count(_ n: Int, _ noun: String) -> String { "\(n) \(noun)\(n == 1 ? "" : "s")" }

        /// Nothing to say about links at all.
        var isEmpty: Bool { notes == 0 && ambiguous == 0 }

        /// The toggle: "Update 3 links in 2 notes".
        var toggleTitle: String { "Update \(Self.count(links, "link")) in \(Self.count(notes, "note"))" }

        /// Under the toggle: what happens either way.
        func detail(updating: Bool) -> String {
            if updating {
                return "Wikilinks to this note will use the new name. Edit ▸ Undo puts them back, "
                    + "as long as none of those notes has changed since."
            }
            return "\(Self.count(links, "link")) in \(Self.count(notes, "note")) will stop working: they'll point to a note that no longer exists."
        }

        /// Notes with unsaved edits aren't changed under the user's hands.
        var unsavedNote: String? {
            guard unsaved > 0 else { return nil }
            return unsaved == 1
                ? "1 note with unsaved edits won't be updated. Save it first to include it."
                : "\(unsaved) notes with unsaved edits won't be updated. Save them first to include them."
        }

        /// Bare links to a name several notes share are left as they are.
        func ambiguousNote(name: String) -> String? {
            guard ambiguous > 0 else { return nil }
            return "\(Self.count(ambiguous, "link")) say\(ambiguous == 1 ? "s" : "") [[\(name)]], and more than one note "
                + "has that name, so \(ambiguous == 1 ? "it isn't" : "they aren't") changed."
        }

        /// The renamed note's own links to itself stay as written while it
        /// has unsaved edits (its draft moves with it untouched).
        static func selfUnsavedNote(selfLinks: Int, hasUnsavedEdits: Bool) -> String? {
            guard hasUnsavedEdits, selfLinks > 0 else { return nil }
            return "This note has unsaved edits, so its \(count(selfLinks, "link")) to itself won't be updated. "
                + "Save it first to include \(selfLinks == 1 ? "it" : "them")."
        }

        /// Another note already has the new name.
        static func collisionNote(newName: String) -> String {
            "Another note is already called \u{201C}\(newName)\u{201D}. Links that say [[\(newName)]] may start pointing to this note."
        }

        /// After the rename: a failed commit, and the linking notes that
        /// still use the old name, and why. nil when there's nothing to say.
        static func outcomeNotice(_ report: KBRenameLinkReport,
                                  commitProblem: String?) -> (title: String, message: String)? {
            var parts: [String] = []
            if let commitProblem {
                let links = report.linksUpdated > 0
                    ? " and \(count(report.linksUpdated, "link")) in \(count(report.notesUpdated, "note")) updated" : ""
                let problem = commitProblem.trimmingCharacters(in: .whitespacesAndNewlines)
                let stop = problem.last.map { ".!?".contains($0) } == true ? "" : "."
                parts.append("The note was renamed\(links), but the git commit failed: \(problem)\(stop) "
                             + "Edit ▸ Undo puts it all back.")
            }
            if !report.skipped.isEmpty {
                let groups: [(KBRenameLinkReport.SkipReason, String)] = [
                    (.unsavedEdits, "unsaved edits"), (.changedMeanwhile, "changed while renaming"),
                    (.unreadable, "couldn't be read"), (.writeFailed, "couldn't be written"),
                ]
                let listed = groups.compactMap { reason, why -> String? in
                    let names = report.skipped.filter { $0.reason == reason }.map { KBNode.displayName(forPath: $0.path) }
                    guard !names.isEmpty else { return nil }
                    let shown = names.prefix(5).joined(separator: ", ") + (names.count > 5 ? " and \(names.count - 5) more" : "")
                    return "\(shown) (\(why))"
                }.joined(separator: "; ")
                let n = report.skipped.count
                parts.append("Not updated: \(listed). \(n == 1 ? "Its" : "Their") links still use the old name; "
                             + "fix them by hand, or undo the rename.")
            }
            guard !parts.isEmpty else { return nil }
            let title = commitProblem != nil ? "Renamed, but not committed" : "Renamed, but some links still use the old name"
            return (title, parts.joined(separator: "\n\n"))
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

    /// A typed name a note can't take, said before Rename is pressed.
    static func renameNameProblem(_ typed: String, for url: URL) -> String? {
        guard url.pathExtension.lowercased() == "md" else { return nil }
        return KnowledgeBaseFileWriter.noteNameProblem(typed)
    }

    private var renameSheet: some View {
        let unsaved = Set(renameInbound.map(\.path).filter {
            service.drafts.draft(for: service.scoutDirectory.appendingPathComponent($0))?.isDirty == true
        })
        let links = RenameLinks.make(inbound: renameInbound, selfLinks: renameSelfLinks, unsavedPaths: unsaved)
        let nameProblem = Self.renameNameProblem(renameText, for: node.url)
        let newStem = (try? KnowledgeBaseFileWriter.renameDestination(for: node.url, to: renameText))
            .map { KnowledgeBaseFileWriter.noteStem($0.lastPathComponent) }
        let collides = isMarkdown && newStem.map {
            $0.lowercased() != node.displayName.lowercased()
                && KBLinkRewrite.newNameCollides($0, oldPath: node.relativePath, index: service.index)
        } == true
        return VStack(alignment: .leading, spacing: 14) {
            Text("Rename note").dsFont(.headline, .serif, weight: .semibold).foregroundStyle(DS.Ink.p1)
            TextField("New name", text: $renameText)
                .accessibilityLabel("New name")
                .textFieldStyle(.roundedBorder).font(DS.font(.body))
                .onSubmit(performRename)
            if let nameProblem {
                Text(nameProblem).font(DS.font(.caption)).foregroundStyle(DS.Status.err)
                    .fixedSize(horizontal: false, vertical: true)
            } else if collides, let newStem {
                Text(RenameLinks.collisionNote(newName: newStem)).font(DS.font(.caption)).foregroundStyle(DS.Status.warn)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !isMarkdown {
                EmptyView()
            } else if links.isEmpty {
                Text("No other notes wikilink here.").font(DS.font(.caption)).foregroundStyle(DS.Ink.p3)
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    if links.notes > 0 {
                        Toggle(links.toggleTitle, isOn: $renameUpdatesLinks)
                            .toggleStyle(.checkbox).font(DS.font(.label))
                        Text(links.detail(updating: renameUpdatesLinks))
                            .font(DS.font(.caption)).foregroundStyle(renameUpdatesLinks ? DS.Ink.p3 : DS.Status.warn)
                            .fixedSize(horizontal: false, vertical: true)
                        if renameUpdatesLinks, let note = links.unsavedNote {
                            Text(note).font(DS.font(.caption)).foregroundStyle(DS.Status.warn)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if renameUpdatesLinks,
                           let note = RenameLinks.selfUnsavedNote(selfLinks: renameSelfLinks, hasUnsavedEdits: isDirty) {
                            Text(note).font(DS.font(.caption)).foregroundStyle(DS.Status.warn)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if let note = links.ambiguousNote(name: node.displayName) {
                        Text(note).font(DS.font(.caption)).foregroundStyle(DS.Status.warn)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            if let renameError {
                Label(renameError, systemImage: "exclamationmark.triangle.fill")
                    .font(DS.font(.caption)).foregroundStyle(DS.Status.err)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                if isRenaming { ProgressView().controlSize(.small).accessibilityLabel("Renaming") }
                Spacer()
                Button("Cancel") { showRename = false }.buttonStyle(.dsSecondary).keyboardShortcut(.cancelAction)
                Button("Rename") { performRename() }.buttonStyle(.dsPrimary).keyboardShortcut(.defaultAction)
                    .disabled(renameText.trimmingCharacters(in: .whitespaces).isEmpty || isRenaming
                              || nameProblem != nil)
            }
        }
        .padding(20).frame(width: 380)
    }

}

/// Plain-text source editor with a monospaced font over the recessed paper
/// surface, so the KB editor reads markdown/YAML source the way the plugin
/// writes it. A text view of its own rather than `TextEditor`, which has no
/// find bar on macOS: ⌘F here opens the system one (UI audit #77).
struct KBSourceEditor: NSViewRepresentable {
    @Binding var text: String
    var find: KBFindController? = nil

    func makeCoordinator() -> Coordinator { Coordinator(text: $text) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        scroll.borderType = .noBorder
        scroll.drawsBackground = true
        scroll.backgroundColor = NSColor(DS.Paper.sunk)
        guard let tv = scroll.documentView as? NSTextView else { return scroll }
        tv.delegate = context.coordinator
        tv.isRichText = false
        tv.allowsUndo = true
        tv.usesFindBar = true
        tv.isIncrementalSearchingEnabled = true
        tv.isAutomaticQuoteSubstitutionEnabled = false
        tv.isAutomaticDashSubstitutionEnabled = false
        tv.isAutomaticTextReplacementEnabled = false
        tv.isAutomaticSpellingCorrectionEnabled = false
        tv.textContainerInset = NSSize(width: 12, height: 10)
        tv.backgroundColor = NSColor(DS.Paper.sunk)
        tv.drawsBackground = true
        tv.string = text
        context.coordinator.applyStyle(to: tv)
        find?.textView = tv
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        context.coordinator.text = $text
        guard let tv = scroll.documentView as? NSTextView else { return }
        find?.textView = tv
        context.coordinator.applyStyle(to: tv)
        if tv.string != text {
            let sel = tv.selectedRange()
            // Typing undo recorded against the old text would replay onto the
            // new one (a reload, or a reverted save).
            context.coordinator.textUndo.removeAllActions()
            tv.string = text
            let len = (text as NSString).length
            tv.setSelectedRange(NSRange(location: min(sel.location, len), length: 0))
        }
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        var text: Binding<String>
        /// The editor's own typing undo, kept apart from the window's, which
        /// holds the vault's undo steps (saves, renames), as in Rich mode.
        let textUndo = UndoManager()
        /// The font and colour last applied. Compared with these, not with
        /// `tv.font`: a note starting with an emoji or CJK reports AppKit's
        /// fallback font there, which would restyle the note on every key.
        private var appliedFont: NSFont?
        private var appliedInk: NSColor?
        init(text: Binding<String>) { self.text = text }

        /// Font and colour, applied on every update too so Settings ▸ General
        /// ▸ Text size reaches an open note without reopening it.
        func applyStyle(to tv: NSTextView) {
            let font = KBLiveEditor.Coordinator.mono(12.5)
            if font != appliedFont { tv.font = font; appliedFont = font }
            let ink = NSColor(DS.Ink.p1)
            if ink != appliedInk { tv.textColor = ink; appliedInk = ink }
        }

        func undoManager(for view: NSTextView) -> UndoManager? { textUndo }

        func textDidChange(_ notification: Notification) {
            guard let tv = notification.object as? NSTextView else { return }
            text.wrappedValue = tv.string
        }
    }
}

/// The note column's layout: its child at the size offered, and never a
/// height taller than the one offered. A banner's text wraps at whatever
/// width it gets, and the split view measures the detail column's minimum
/// size in its own hosting view at a near-zero width (the window's 1070 pt
/// minimum frame doesn't reach it). There the "Unsaved draft" banner asked
/// for about 1,500 pt and the "changed on disk" one about 2,300 pt, and the
/// split view centred that overflow, pushing the window's content up by half
/// of it. Width passes through unchanged, so the page's minimum width
/// doesn't move.
struct KBNoteColumnLayout: Layout {
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        guard let child = subviews.first else { return .zero }
        let size = child.sizeThatFits(proposal)
        guard let offered = proposal.height else { return size }
        return CGSize(width: size.width, height: min(size.height, offered))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        for child in subviews {
            child.place(at: bounds.origin, anchor: .topLeading, proposal: ProposedViewSize(bounds.size))
        }
    }
}

/// A crumb that never gets narrower than `floor` (or its own width, when
/// that's less), so a squeezed folder path truncates to "p…s" instead of a
/// clipped glyph, and a short one gets no padding.
struct KBCrumbFloorLayout: Layout {
    let floor: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        guard let child = subviews.first else { return .zero }
        return child.sizeThatFits(widened(proposal, child))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        for child in subviews {
            child.place(at: CGPoint(x: bounds.minX, y: bounds.midY), anchor: .leading,
                        proposal: ProposedViewSize(bounds.size))
        }
    }

    private func widened(_ proposal: ProposedViewSize, _ child: LayoutSubview) -> ProposedViewSize {
        guard let width = proposal.width else { return proposal }
        let natural = child.sizeThatFits(.unspecified).width
        return ProposedViewSize(width: max(width, min(floor, natural)), height: proposal.height)
    }
}
