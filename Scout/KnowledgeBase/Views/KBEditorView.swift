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
    /// Called when the user clicks the breadcrumb root — parent clears the
    /// selection, returning to the overview (stats + global graph).
    let onOverview: () -> Void

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

    private var isDirty: Bool { draft != originalText }
    private var isMarkdown: Bool { node.ext == "md" }

    var body: some View {
        VStack(spacing: 0) {
            header
            EditorialRule()
            if externallyChanged { changedOnDiskBanner }
            content
        }
        .background(Color.clear)
        .onChange(of: node.id) { _, _ in loadFile() }
        .onAppear { loadFile() }
        // Service reparses on FSEvents; re-check whether our open file moved
        // out from under us.
        .onReceive(service.objectWillChange) { _ in
            DispatchQueue.main.async { detectExternalChange() }
        }
        .alert("File changed on disk", isPresented: $showConflict) {
            Button("Overwrite", role: .destructive) { forceSave() }
            Button("Reload", role: .cancel) { loadFile() }
        } message: {
            Text("\(node.name) was modified by another process since you opened it. Overwrite it with your version, or reload to discard your changes?")
        }
        .alert("Couldn't save", isPresented: Binding(
            get: { errorMessage != nil },
            set: { if !$0 { errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
        .alert("Delete \(node.displayName)?", isPresented: $showDeleteConfirm) {
            Button("Delete", role: .destructive) { performDelete() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This removes \(node.relativePath) and commits the deletion. This can't be undone from the app.")
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

            Button { loadFile() } label: { Label("Reload", systemImage: "arrow.clockwise") }
                .buttonStyle(.plain).foregroundStyle(DS.Ink.p3).font(DS.sans(12))
                .help("Reload from disk (discards unsaved changes)")

            Button(action: save) {
                Label("Save", systemImage: "checkmark")
                    .font(DS.sans(12, weight: .semibold))
            }
            .buttonStyle(.plain)
            .foregroundStyle(isDirty && !isSaving ? .white : DS.Ink.p4)
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(Capsule().fill(isDirty && !isSaving ? DS.Accent.fill : DS.Paper.sunk))
            .disabled(!isDirty || isSaving)
            .keyboardShortcut("s", modifiers: .command)

            Menu {
                Button("Rename…") { renameText = node.displayName; showRename = true }
                Button("Reveal in Finder") { NSWorkspace.shared.activateFileViewerSelecting([node.url]) }
                Divider()
                Button("Delete…", role: .destructive) { showDeleteConfirm = true }
            } label: {
                Image(systemName: "ellipsis.circle").font(.system(size: 15)).foregroundStyle(DS.Ink.p3)
            }
            .menuStyle(.borderlessButton).frame(width: 28)
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
            Button("Reload") { loadFile() }.buttonStyle(.plain)
                .font(DS.sans(12, weight: .semibold)).foregroundStyle(DS.Accent.ink)
        }
        .padding(.horizontal, 20).padding(.vertical, 8)
        .background(DS.Accent.wash)
    }

    // MARK: - Content

    @ViewBuilder
    private var content: some View {
        if !isMarkdown {
            KBSourceEditor(text: $draft)
        } else {
            switch mode {
            case .read:
                // Rendered, but editable in place: double-click a paragraph,
                // heading, list item or table cell to edit just that piece.
                KBEditableView(source: $draft)
            case .rich:
                KBLiveEditor(text: $draft)
            case .source:
                KBSourceEditor(text: $draft)
            }
        }
    }

    // MARK: - Actions

    private func loadFile() {
        let text = service.readFile(node.url)
        draft = text ?? ""
        originalText = draft
        baselineContents = text
        baselineDate = GuardedFileWrite.fsModificationDate(node.url)
        externallyChanged = false
        if !isMarkdown { mode = .source }
    }

    /// Detect an external change to the open file: the mtime is the cheap
    /// trigger, a content compare confirms (so the app's own atomic save or a
    /// same-content touch doesn't flag). Silently reloads if the user has no
    /// unsaved edits; otherwise flags the banner.
    private func detectExternalChange() {
        guard !isSaving else { return }
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
        guard isDirty, !isSaving else { return }
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
                try await writer.save(fileURL: node.url, contents: contents,
                                      baselineContents: captured, label: node.displayName)
                await MainActor.run { markSaved(contents) }
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
                    errorMessage = KBWriterError.message(for: error)
                }
            }
        }
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
                try await writer.delete(fileURL: node.url, label: node.displayName)
                await MainActor.run { service.reload(); onDeleted() }
            } catch {
                await MainActor.run {
                    // The removal itself succeeded when only the commit failed.
                    if case KBWriterError.commitFailed = error { service.reload(); onDeleted() }
                    errorMessage = KBWriterError.message(for: error)
                }
            }
        }
    }

    private func performRename() {
        let newName = renameText
        Task {
            do {
                let dest = try await writer.rename(fileURL: node.url, to: newName)
                await MainActor.run { service.reload(); onRenamed(dest); showRename = false }
            } catch {
                await MainActor.run {
                    // The move itself succeeded when only the commit failed.
                    if case KBWriterError.commitFailed = error { service.reload() }
                    errorMessage = KBWriterError.message(for: error)
                    showRename = false
                }
            }
        }
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
