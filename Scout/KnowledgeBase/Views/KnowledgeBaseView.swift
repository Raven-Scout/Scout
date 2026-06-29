import SwiftUI

/// Knowledge Base tab: a two-pane file browser + editor over
/// `~/Scout/knowledge-base/`. Left is the file tree (with search and "New
/// note"); right edits the selected file. All writes go through
/// `KnowledgeBaseFileWriter` (atomic + git-committed).
struct KnowledgeBaseView: View {
    @EnvironmentObject var service: KnowledgeBaseService
    @EnvironmentObject var writerBox: KnowledgeBaseWriterBox

    @State private var selectedPath: String? = nil
    @State private var expanded: Set<String> = []
    @State private var searchQuery: String = ""
    @State private var showNewFile = false
    @State private var errorMessage: String? = nil

    private var selectedNode: KBNode? {
        guard let selectedPath else { return nil }
        return Self.findNode(path: selectedPath, in: service.tree)
    }

    var body: some View {
        HStack(spacing: 0) {
            leftPane
                .frame(width: 268)
            Rectangle().fill(DS.Rule.soft).frame(width: 0.5)
            rightPane
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .onAppear { service.load() }
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

    // MARK: - Left pane

    private var leftPane: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Text("Knowledge Base")
                    .font(DS.serif(15, weight: .semibold)).foregroundStyle(DS.Ink.p1)
                Spacer()
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
        case .missing:
            emptyState(icon: "folder.badge.questionmark",
                       title: "No knowledge base",
                       detail: "Expected \(service.kbDirectory.path). Install the scout-plugin and run /scout-setup.")
        case .failed(let msg):
            emptyState(icon: "exclamationmark.triangle", title: "Couldn't read the knowledge base", detail: msg)
        default:
            if service.tree.isEmpty {
                emptyState(icon: "doc.text", title: "Empty knowledge base",
                           detail: "No notes yet. Use + to create the first one.")
            } else {
                KBTreeView(nodes: service.tree, selectedPath: $selectedPath, expanded: $expanded)
            }
        }
    }

    private var searchResults: some View {
        let q = searchQuery.lowercased()
        let matches = service.tree.flatMap(\.allFiles)
            .filter { $0.displayName.lowercased().contains(q) || $0.relativePath.lowercased().contains(q) }
            .sorted { $0.displayName.localizedCaseInsensitiveCompare($1.displayName) == .orderedAscending }
        return ScrollView {
            LazyVStack(alignment: .leading, spacing: 1) {
                if matches.isEmpty {
                    Text("No matches").font(DS.sans(12)).foregroundStyle(DS.Ink.p4)
                        .padding(.horizontal, 12).padding(.vertical, 10)
                }
                ForEach(matches) { node in
                    Button { selectFromSearch(node) } label: {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(node.displayName).font(DS.sans(12.5, weight: .medium)).foregroundStyle(DS.Ink.p1)
                            Text(node.relativePath).font(DS.mono(10)).foregroundStyle(DS.Ink.p4).lineLimit(1)
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

    // MARK: - Right pane

    @ViewBuilder
    private var rightPane: some View {
        if let node = selectedNode, node.isEditable {
            KBEditorView(
                node: node,
                service: service,
                writer: writerBox.writer,
                onDeleted: { selectedPath = nil },
                onRenamed: { url in
                    selectedPath = KnowledgeBaseService.relativePath(of: url, in: service.scoutDirectory)
                }
            )
            .id(node.id)
        } else {
            emptyState(icon: "doc.richtext",
                       title: "Select a note",
                       detail: "Pick a file from the tree to read or edit it.")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
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

    private func selectFromSearch(_ node: KBNode) {
        // Expand ancestors so the file is visible when search clears.
        let parts = node.relativePath.components(separatedBy: "/")
        for i in 1..<max(1, parts.count) {
            expanded.insert(parts[0..<i].joined(separator: "/"))
        }
        selectedPath = node.relativePath
        searchQuery = ""
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
                    let rel = KnowledgeBaseService.relativePath(of: dest, in: service.scoutDirectory)
                    let parts = rel.components(separatedBy: "/")
                    for i in 1..<max(1, parts.count) {
                        expanded.insert(parts[0..<i].joined(separator: "/"))
                    }
                    selectedPath = rel
                }
            } catch {
                await MainActor.run { errorMessage = describe(error); showNewFile = false }
            }
        }
    }

    private func describe(_ error: Error) -> String {
        switch error {
        case KBWriterError.alreadyExists(let n): return "A file named \(n) already exists."
        case KBWriterError.emptyName: return "The name can't be empty."
        case KBWriterError.writeFailed(let m): return m
        default: return error.localizedDescription
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
