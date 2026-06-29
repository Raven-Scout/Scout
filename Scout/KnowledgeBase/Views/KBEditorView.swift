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

    private enum Mode: String { case preview, edit }

    @State private var draft: String = ""
    @State private var originalText: String = ""
    @State private var baseline: Date? = nil
    // Business users read first: markdown opens rendered, editing is one click
    // away. loadFile() forces .edit for non-markdown (YAML) where there is no
    // preview renderer.
    @State private var mode: Mode = .preview
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
                    options: [(label: "Preview", value: .preview), (label: "Edit", value: .edit)]
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
                Text((part as NSString).deletingPathExtension.isEmpty ? part : (i == parts.count - 1 ? (part as NSString).deletingPathExtension : part))
                    .font(DS.sans(11, weight: i == parts.count - 1 ? .semibold : .regular))
                    .foregroundStyle(i == parts.count - 1 ? DS.Ink.p1 : DS.Ink.p3)
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
        if isMarkdown && mode == .preview {
            ScrollView {
                KBMarkdownPreview(source: draft)
                    .padding(.horizontal, 24).padding(.vertical, 18)
            }
        } else {
            KBSourceEditor(text: $draft)
        }
    }

    // MARK: - Actions

    private func loadFile() {
        let text = service.readFile(node.url) ?? ""
        draft = text
        originalText = text
        baseline = GuardedFileWrite.fsModificationDate(node.url)
        externallyChanged = false
        if !isMarkdown { mode = .edit }
    }

    /// Compare the open file's on-disk mtime against our baseline. Silently
    /// reloads if the user has no unsaved edits; otherwise flags the banner.
    private func detectExternalChange() {
        guard FileManager.default.fileExists(atPath: node.url.path) else { return }
        let current = GuardedFileWrite.fsModificationDate(node.url)
        guard let current, let baseline,
              abs(current.timeIntervalSince(baseline)) > 0.0005 else { return }
        if isDirty {
            externallyChanged = true
        } else {
            loadFile()
        }
    }

    private func save() {
        guard isDirty, !isSaving else { return }
        isSaving = true
        let contents = draft
        let captured = baseline
        Task {
            do {
                try await writer.save(fileURL: node.url, contents: contents,
                                      baseline: captured, label: node.displayName)
                await MainActor.run {
                    originalText = contents
                    baseline = GuardedFileWrite.fsModificationDate(node.url)
                    externallyChanged = false
                    isSaving = false
                }
            } catch KBWriterError.conflict {
                await MainActor.run { isSaving = false; showConflict = true }
            } catch {
                await MainActor.run { isSaving = false; errorMessage = describe(error) }
            }
        }
    }

    /// Overwrite despite a detected conflict — re-baseline to the current disk
    /// mtime so the guard passes, then save.
    private func forceSave() {
        let contents = draft
        let current = GuardedFileWrite.fsModificationDate(node.url)
        isSaving = true
        Task {
            do {
                try await writer.save(fileURL: node.url, contents: contents,
                                      baseline: current, label: node.displayName)
                await MainActor.run {
                    originalText = contents
                    baseline = GuardedFileWrite.fsModificationDate(node.url)
                    externallyChanged = false
                    isSaving = false
                }
            } catch {
                await MainActor.run { isSaving = false; errorMessage = describe(error) }
            }
        }
    }

    private func performDelete() {
        Task {
            do {
                try await writer.delete(fileURL: node.url, label: node.displayName)
                await MainActor.run { service.reload(); onDeleted() }
            } catch {
                await MainActor.run { errorMessage = describe(error) }
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
                await MainActor.run { errorMessage = describe(error); showRename = false }
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

    private func describe(_ error: Error) -> String {
        switch error {
        case KBWriterError.alreadyExists(let n): return "A file named \(n) already exists."
        case KBWriterError.notFound(let n): return "\(n) no longer exists."
        case KBWriterError.writeFailed(let m): return m
        case KBWriterError.readFailed(let m): return m
        case KBWriterError.outsideKnowledgeBase(let n): return "\(n) is outside the knowledge base."
        case KBWriterError.emptyName: return "The name can't be empty."
        case KBWriterError.conflict(let f): return "\(f) changed on disk."
        default: return error.localizedDescription
        }
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
