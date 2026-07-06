import SwiftUI
import AppKit

/// Knowledge Base tab: a file browser + editor + links/graph panel over
/// `~/Scout/knowledge-base/`. Left is the tree (with full-text search and "New
/// note"); center reads/edits the selected file (or shows an overview); right
/// shows the note's links and local graph. All writes go through
/// `KnowledgeBaseFileWriter` (atomic + git-committed).
struct KnowledgeBaseView: View {
    @EnvironmentObject var service: KnowledgeBaseService
    @EnvironmentObject var writerBox: KnowledgeBaseWriterBox

    @State private var selectedPath: String? = nil
    @State private var expanded: Set<String> = []
    @State private var searchQuery: String = ""
    @State private var searchHits: [KBSearchHit] = []
    @State private var searchTask: Task<Void, Never>? = nil
    @State private var showNewFile = false
    @State private var errorMessage: String? = nil
    /// Width of the Links/Graph panel — drag its left edge to resize. Persisted.
    @AppStorage("kbRightPanelWidth") private var rightPanelWidth: Double = 300

    private var selectedNode: KBNode? {
        guard let selectedPath else { return nil }
        return Self.findNode(path: selectedPath, in: service.tree)
    }

    var body: some View {
        HStack(spacing: 0) {
            leftPane.frame(width: 268)
            divider
            centerPane.frame(maxWidth: .infinity, maxHeight: .infinity)
            if let node = selectedNode, node.isEditable {
                PaneResizeHandle(width: $rightPanelWidth, minWidth: 220, maxWidth: 640)
                KBRightPanel(relPath: node.relativePath, service: service,
                             onNavigate: { navigate(toPath: $0) })
                    .frame(width: CGFloat(rightPanelWidth))
            }
        }
        // In-app wikilink navigation for every pane (editor, backlink excerpts,
        // search snippets): resolve against the index, fall back to the default
        // Linear/Obsidian opening when the target isn't a KB note.
        .environment(\.kbWikilinkHandler, { target in
            if let path = service.resolveWikilink(target) {
                navigate(toPath: path)
                return true
            }
            return false
        })
        .onAppear { service.load() }
        .onChange(of: searchQuery) { _, q in scheduleSearch(q) }
        .sheet(isPresented: $showNewFile) {
            KBNewFileSheet(
                directories: Self.directories(in: service.tree, kbRoot: service.kbDirectory),
                defaultDirectory: defaultNewFileDirectory(),
                onCreate: createFile
            )
        }
        .alert("Couldn't create note", isPresented: Binding(
            get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) { errorMessage = nil }
        } message: { Text(errorMessage ?? "") }
    }

    private var divider: some View { Rectangle().fill(DS.Rule.soft).frame(width: 0.5) }

    // MARK: - Left pane

    private var leftPane: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Text("Knowledge Base")
                    .font(DS.serif(15, weight: .semibold)).foregroundStyle(DS.Ink.p1)
                Spacer()
                if selectedPath != nil {
                    Button { selectedPath = nil } label: {
                        Image(systemName: "house").font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(DS.Ink.p3)
                    }
                    .buttonStyle(.plainHit).help("Back to overview")
                }
                Button { showNewFile = true } label: {
                    Image(systemName: "plus").font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(DS.Accent.ink)
                }
                .buttonStyle(.plainHit).help("New note")
            }
            .padding(.horizontal, 14).padding(.top, 14).padding(.bottom, 8)

            searchField
                .padding(.horizontal, 12).padding(.bottom, 8)

            EditorialRule()

            if searchQuery.isEmpty {
                treeOrState
            } else {
                searchResults
            }
        }
        .background(
            LinearGradient(colors: [DS.Paper.sunk, DS.Paper.base],
                           startPoint: .leading, endPoint: .trailing)
        )
    }

    private var searchField: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").font(.system(size: 11)).foregroundStyle(DS.Ink.p4)
            TextField("Search notes…", text: $searchQuery)
                .textFieldStyle(.plain).font(DS.sans(12.5)).foregroundStyle(DS.Ink.p1)
            if !searchQuery.isEmpty {
                Button { searchQuery = "" } label: {
                    Image(systemName: "xmark.circle.fill").font(.system(size: 11)).foregroundStyle(DS.Ink.p4)
                }.buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .neumorphicPressed(cornerRadius: 7)
    }

    @ViewBuilder
    private var treeOrState: some View {
        switch service.state {
        case .idle, .loading:
            // First scan runs off the main actor — show nothing rather than
            // flashing "Empty knowledge base" before it lands.
            Spacer()
        case .missing:
            emptyState(icon: "folder.badge.questionmark",
                       title: "No knowledge base",
                       detail: "Expected \(service.kbDirectory.path). Install the scout-plugin and run /scout-setup.")
        case .failed(let msg):
            emptyState(icon: "exclamationmark.triangle", title: "Couldn't read the knowledge base", detail: msg)
        case .loaded:
            if service.tree.isEmpty {
                emptyState(icon: "doc.text", title: "Empty knowledge base",
                           detail: "No notes yet. Use + to create the first one.")
            } else {
                KBTreeView(nodes: service.tree, selectedPath: $selectedPath, expanded: $expanded)
            }
        }
    }

    private var searchResults: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 1) {
                if searchHits.isEmpty {
                    Text(searchQuery.count < 2 ? "Type to search…" : "No results")
                        .font(DS.sans(12)).foregroundStyle(DS.Ink.p4)
                        .padding(.horizontal, 12).padding(.vertical, 10)
                }
                ForEach(searchHits) { hit in
                    Button { navigate(toPath: hit.path) } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(hit.name).font(DS.sans(12.5, weight: .medium)).foregroundStyle(DS.Ink.p1)
                            Text(hit.path).font(DS.mono(10)).foregroundStyle(DS.Ink.p4).lineLimit(1)
                            if !hit.snippet.isEmpty {
                                InlineMarkdownText(hit.snippet)
                                    .font(DS.sans(10.5)).foregroundStyle(DS.Ink.p3)
                                    .lineLimit(2).multilineTextAlignment(.leading)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plainHit)
                }
            }
            .padding(.horizontal, 8).padding(.vertical, 8)
        }
    }

    // MARK: - Center pane

    @ViewBuilder
    private var centerPane: some View {
        if let node = selectedNode, node.isEditable {
            KBEditorView(
                node: node,
                service: service,
                writer: writerBox.writer,
                onDeleted: { selectedPath = nil },
                onRenamed: { url in
                    selectedPath = KnowledgeBaseService.relativePath(of: url, in: service.scoutDirectory)
                },
                onOverview: { selectedPath = nil }
            )
            .id(node.id)
        } else {
            KBOverviewView(service: service, onNavigate: { navigate(toPath: $0) })
        }
    }

    private func emptyState(icon: String, title: String, detail: String) -> some View {
        VStack(spacing: 10) {
            Image(systemName: icon).font(.system(size: 30)).foregroundStyle(DS.Ink.p4)
            Text(title).font(DS.serif(16, weight: .semibold)).foregroundStyle(DS.Ink.p2)
            Text(detail).font(DS.sans(12)).foregroundStyle(DS.Ink.p4)
                .multilineTextAlignment(.center).frame(maxWidth: 320)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }

    // MARK: - Actions

    /// Select a note by path, expanding its ancestor folders and clearing search.
    private func navigate(toPath path: String) {
        let parts = path.components(separatedBy: "/")
        if parts.count > 1 {
            for i in 1..<parts.count { expanded.insert(parts[0..<i].joined(separator: "/")) }
        }
        selectedPath = path
        searchQuery = ""
    }

    private func scheduleSearch(_ query: String) {
        searchTask?.cancel()
        guard query.count >= 2 else { searchHits = []; return }
        searchTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 200_000_000)
            if Task.isCancelled { return }
            searchHits = service.searchContent(query)
        }
    }

    /// Directory the New-note sheet defaults to: the folder of the current
    /// selection, else the KB root.
    private func defaultNewFileDirectory() -> URL {
        if let node = selectedNode {
            return node.isDirectory ? node.url : node.url.deletingLastPathComponent()
        }
        return service.kbDirectory
    }

    private func createFile(name: String, directory: URL) {
        Task {
            do {
                let slug = (name as NSString).deletingPathExtension
                let title = slug.replacingOccurrences(of: "-", with: " ")
                let initial = "# \(title)\n\n"
                let dest = try await writerBox.writer.createFile(
                    in: directory, name: name, initialContents: initial)
                await MainActor.run {
                    service.reload()
                    showNewFile = false
                    navigate(toPath: KnowledgeBaseService.relativePath(of: dest, in: service.scoutDirectory))
                }
            } catch {
                await MainActor.run {
                    // The file itself was created when only the commit failed.
                    if case KBWriterError.commitFailed = error { service.reload() }
                    errorMessage = KBWriterError.message(for: error)
                    showNewFile = false
                }
            }
        }
    }

    // MARK: - Tree helpers

    static func findNode(path: String, in nodes: [KBNode]) -> KBNode? {
        for node in nodes {
            if node.relativePath == path { return node }
            if node.isDirectory, let hit = findNode(path: path, in: node.children) { return hit }
        }
        return nil
    }

    /// All directory nodes (plus the KB root) for the New-note folder picker.
    static func directories(in nodes: [KBNode], kbRoot: URL) -> [(label: String, url: URL)] {
        var result: [(label: String, url: URL)] = [(label: "knowledge-base", url: kbRoot)]
        func walk(_ nodes: [KBNode]) {
            for node in nodes where node.isDirectory {
                result.append((label: node.relativePath, url: node.url))
                walk(node.children)
            }
        }
        walk(nodes)
        return result
    }
}

/// Sheet for creating a new note: name + target folder.
struct KBNewFileSheet: View {
    let directories: [(label: String, url: URL)]
    let defaultDirectory: URL
    let onCreate: (_ name: String, _ directory: URL) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var directoryPath: String = ""

    private var selectedURL: URL {
        directories.first { $0.url.path == directoryPath }?.url ?? defaultDirectory
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("New note").font(DS.serif(16, weight: .semibold)).foregroundStyle(DS.Ink.p1)

            VStack(alignment: .leading, spacing: 4) {
                Text("FOLDER").font(DS.sans(10, weight: .medium)).tracking(0.6).foregroundStyle(DS.Ink.p4)
                Picker("", selection: $directoryPath) {
                    ForEach(directories, id: \.url.path) { dir in
                        Text(dir.label).tag(dir.url.path)
                    }
                }
                .labelsHidden().pickerStyle(.menu)
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("NAME").font(DS.sans(10, weight: .medium)).tracking(0.6).foregroundStyle(DS.Ink.p4)
                TextField("my-note", text: $name)
                    .textFieldStyle(.roundedBorder).font(DS.sans(13))
                    .onSubmit(create)
                Text(".md is added automatically").font(DS.sans(10.5)).foregroundStyle(DS.Ink.p4)
            }

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Create") { create() }.keyboardShortcut(.defaultAction)
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20).frame(width: 380)
        .onAppear { directoryPath = defaultDirectory.path }
    }

    private func create() {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        onCreate(trimmed, selectedURL)
    }
}

/// A draggable vertical divider that resizes the pane to its right. Drag left to
/// widen, right to narrow; shows a resize cursor on hover. Width is clamped to
/// [minWidth, maxWidth].
struct PaneResizeHandle: View {
    @Binding var width: Double
    let minWidth: Double
    let maxWidth: Double

    @State private var dragStartWidth: Double? = nil

    var body: some View {
        Rectangle()
            .fill(DS.Rule.soft)
            .frame(width: 0.5)
            .overlay(
                Rectangle()
                    .fill(Color.clear)
                    .frame(width: 9)
                    .contentShape(Rectangle())
                    .onHover { inside in
                        if inside { NSCursor.resizeLeftRight.push() } else { NSCursor.pop() }
                    }
                    .gesture(
                        DragGesture(coordinateSpace: .global)
                            .onChanged { value in
                                let base = dragStartWidth ?? width
                                if dragStartWidth == nil { dragStartWidth = width }
                                // Divider sits on the pane's left edge: dragging
                                // left (negative dx) widens the pane.
                                width = min(maxWidth, max(minWidth, base - Double(value.translation.width)))
                            }
                            .onEnded { _ in dragStartWidth = nil }
                    )
            )
    }
}
