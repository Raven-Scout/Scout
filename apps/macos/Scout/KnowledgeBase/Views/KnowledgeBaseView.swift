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
    /// A note another tab asked for (`AppState.requestOpenKBNote`), opened
    /// once the index is built, then cleared.
    var pendingNote: Binding<String?> = .constant(nil)
    /// Privacy labels for the tree's lock badge (#453); nil shows no badges.
    var privacy: KBPrivacyLabelService? = nil

    @State private var selectedPath: String? = nil
    /// The note the overview map should select ("Show on full map").
    @State private var mapFocus: String? = nil
    @State private var expanded: Set<String> = []
    @State private var searchQuery: String = ""
    @State private var searchHits: [KBSearchHit] = []
    @State private var searchTask: Task<Void, Never>? = nil
    @FocusState private var searchFocused: Bool
    @State private var showNewFile = false
    @State private var errorMessage: String? = nil
    /// A rename that moved the note but couldn't commit it. Shown here, not in
    /// the editor: the editor is replaced when the note reopens at its new path.
    @State private var renameProblem: String? = nil
    /// A rename that worked but left some linking notes on the old name.
    @State private var renameNotice: (title: String, message: String)? = nil
    /// Width of the Links/Graph panel — drag its left edge to resize. Persisted.
    @AppStorage("kbRightPanelWidth") private var rightPanelWidth: Double = 300
    /// The page's width, so the Links/Graph panel never squeezes the editor.
    @State private var pageWidth: CGFloat?

    static let treeWidth: CGFloat = 268
    static let minEditorWidth: CGFloat = 380
    static let rightPanelMin: CGFloat = 220
    static let rightPanelMax: CGFloat = 640

    /// The Links/Graph panel's width: the stored one, narrowed when the page
    /// can't fit it beside the tree and a `minEditorWidth` editor. At the
    /// minimum window a wide stored panel left the editor ~310 pt (UI audit C6).
    nonisolated static func rightPanelWidth(stored: Double, pageWidth: CGFloat?) -> CGFloat {
        let wanted = min(max(CGFloat(stored), rightPanelMin), rightPanelMax)
        guard let pageWidth else { return wanted }
        // Tree, its divider, the resize handle (8) and the editor.
        let room = pageWidth - treeWidth - 1 - 8 - minEditorWidth
        return max(rightPanelMin, min(wanted, room))
    }

    /// The narrowest the tree gets when a note and its Links/Graph panel are open.
    static let minTreeWidth: CGFloat = 200

    /// The tree's width: `treeWidth`, narrowed (to `minTreeWidth` at most)
    /// when a note's Links/Graph panel is open on a page too narrow for the
    /// tree, a `minEditorWidth` editor and a `rightPanelMin` panel side by
    /// side, which a window near the 1070 pt minimum is (UI audit #8).
    nonisolated static func treeWidth(pageWidth: CGFloat?, panelShown: Bool) -> CGFloat {
        guard panelShown, let pageWidth else { return treeWidth }
        let room = pageWidth - 1 - 8 - minEditorWidth - rightPanelMin
        return max(minTreeWidth, min(treeWidth, room))
    }

    private var selectedNode: KBNode? {
        guard let selectedPath else { return nil }
        return Self.findNode(path: selectedPath, in: service.tree)
    }

    var body: some View {
        let panelShown = selectedNode?.isEditable == true
        HStack(spacing: 0) {
            leftPane.frame(width: Self.treeWidth(pageWidth: pageWidth, panelShown: panelShown))
            divider
            centerPane.frame(maxWidth: .infinity, maxHeight: .infinity)
            if let node = selectedNode, node.isEditable {
                // The handle starts a drag from the width on screen, not the
                // stored one: when the page clamps a wide stored panel, a drag
                // from the stored width would do nothing until it caught up.
                PaneResizeHandle(
                    width: Binding(
                        get: { Double(Self.rightPanelWidth(stored: rightPanelWidth, pageWidth: pageWidth)) },
                        set: { rightPanelWidth = $0 }),
                    minWidth: Self.rightPanelMin, maxWidth: Self.rightPanelMax)
                KBRightPanel(relPath: node.relativePath, service: service,
                             onNavigate: { navigate(toPath: $0) },
                             onShowOnMap: { path in
                                 mapFocus = path
                                 selectedPath = nil
                             })
                    .frame(width: Self.rightPanelWidth(stored: rightPanelWidth, pageWidth: pageWidth))
            }
        }
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { pageWidth = $0 }
        // ⇧⌘F searches every note from anywhere on the page; ⌘F does too
        // until a note is open, when it finds in that note instead (UI audit
        // #77; `KBEditorView` binds it then).
        .background {
            Group {
                Button("Search Notes") { searchFocused = true }
                    .keyboardShortcut(AppShortcut.searchNotes)
                if selectedNode?.isEditable != true {
                    Button("Find") { searchFocused = true }
                        .keyboardShortcut(AppShortcut.find)
                }
            }
            .frame(width: 0, height: 0)
            .opacity(0)
            .accessibilityHidden(true)
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
        // Clicking a [#TAG] chip searches the KB for that tag: drive the same
        // search field a typed query uses, so the query is visible, the results
        // list is the familiar one, and clearing it returns to the tree.
        .environment(\.kbTagHandler, { tag in searchQuery = "#\(tag)" })
        .onAppear {
            service.load()
            openPendingNote()
        }
        .onChange(of: service.state) { _, _ in openPendingNote() }
        .onChange(of: pendingNote.wrappedValue) { _, _ in openPendingNote() }
        // A request still waiting on the index when the user leaves is
        // dropped, so it can't fire on some later visit.
        .onDisappear { pendingNote.wrappedValue = nil }
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
        .alert("Renamed, but not committed", isPresented: Binding(
            get: { renameProblem != nil }, set: { if !$0 { renameProblem = nil } }
        )) {
            Button("OK", role: .cancel) { renameProblem = nil }
        } message: { Text(renameProblem ?? "") }
        .alert(renameNotice?.title ?? "", isPresented: Binding(
            get: { renameNotice != nil }, set: { if !$0 { renameNotice = nil } }
        )) {
            Button("OK", role: .cancel) { renameNotice = nil }
        } message: { Text(renameNotice?.message ?? "") }
    }

    /// Whether the tree shows its loading state: the first scan hasn't
    /// landed yet. A rescan of a tree already on screen (every return to the
    /// page calls `load()`) keeps the tree, so it never blanks (UI audit #76).
    nonisolated static func showsLoading(_ state: KnowledgeBaseService.State, treeIsEmpty: Bool) -> Bool {
        switch state {
        case .idle, .loading: return treeIsEmpty
        default: return false
        }
    }

    /// The loading state the tree and the overview show while the first
    /// scan runs, instead of a blank pane or "0 notes".
    struct LoadingState: View {
        var body: some View {
            VStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text("Loading notes…").font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Loading notes")
        }
    }

    private var divider: some View { Rectangle().fill(DS.Rule.soft).frame(width: 0.5) }

    // MARK: - Left pane

    private var leftPane: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Text("Knowledge Base")
                    .font(DS.serif(15, weight: .semibold)).foregroundStyle(DS.Ink.p1)
                    .glossaryHelp(.knowledgeBase)
                Spacer()
                if selectedPath != nil {
                    Button { selectedPath = nil } label: {
                        Image(systemName: "house").font(DS.sans(11, weight: .semibold))
                            .foregroundStyle(DS.Ink.p3)
                    }
                    .buttonStyle(.plainHit).help("Back to overview")
                    .accessibilityLabel("Back to overview")
                }
                Button { showNewFile = true } label: {
                    Image(systemName: "plus").font(DS.sans(12, weight: .semibold))
                        .foregroundStyle(DS.Accent.ink)
                }
                .buttonStyle(.plainHit).help("New note") // ds-guard: hit area (an icon button)
                .accessibilityLabel("New note")
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
            Image(systemName: "magnifyingglass").font(DS.sans(11)).foregroundStyle(DS.Ink.p4)
            TextField("Search notes…", text: $searchQuery)
                .textFieldStyle(.plain).font(DS.sans(12.5)).foregroundStyle(DS.Ink.p1)
                .focused($searchFocused)
                .help(ShortcutLabel.help("Search every note", AppShortcut.searchNotes))
            if !searchQuery.isEmpty {
                Button { searchQuery = "" } label: {
                    Image(systemName: "xmark.circle.fill").font(DS.sans(11)).foregroundStyle(DS.Ink.p4)
                }.buttonStyle(.plain)
                .accessibilityLabel("Clear search")
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .neumorphicPressed(cornerRadius: 7)
    }

    @ViewBuilder
    private var treeOrState: some View {
        switch service.state {
        case .idle, .loading:
            // The first scan runs off the main actor: say so rather than
            // flashing "Empty knowledge base" before it lands. A rescan keeps
            // the tree already on screen.
            if Self.showsLoading(service.state, treeIsEmpty: service.tree.isEmpty) {
                LoadingState()
            } else {
                tree
            }
        case .missing:
            emptyState(icon: "folder.badge.questionmark",
                       title: "No knowledge base",
                       detail: "Expected \(service.kbDirectory.path). Setting up the vault creates it: see Settings ▸ Engine — or, for an engine managed in Claude Code, run /scout-setup there.")
        case .failed(let msg):
            emptyState(icon: "exclamationmark.triangle", title: "Couldn't read the knowledge base", detail: msg)
        case .loaded:
            if service.tree.isEmpty {
                emptyState(icon: "doc.text", title: "Empty knowledge base",
                           detail: "No notes yet. Use + to create the first one.")
            } else {
                tree
            }
        }
    }

    @ViewBuilder
    private var tree: some View {
        if let privacy {
            KBBadgedTreeView(service: service, privacy: privacy,
                             selectedPath: $selectedPath, expanded: $expanded)
        } else {
            KBTreeView(nodes: service.tree, selectedPath: $selectedPath, expanded: $expanded)
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
                onRenameProblem: { renameProblem = $0 },
                onRenameNotice: { renameNotice = ($0, $1) },
                onOverview: { selectedPath = nil },
                onMoved: { from, to in
                    // Follow an undone or redone rename only if the note is
                    // still the one on screen.
                    let fromPath = KnowledgeBaseService.relativePath(of: from, in: service.scoutDirectory)
                    if selectedPath == fromPath {
                        selectedPath = KnowledgeBaseService.relativePath(of: to, in: service.scoutDirectory)
                    }
                }
            )
            .id(node.id)
        } else {
            KBOverviewView(service: service, onNavigate: { navigate(toPath: $0) }, mapFocus: mapFocus)
        }
    }

    private func emptyState(icon: String, title: String, detail: String) -> some View {
        VStack(spacing: 10) {
            Image(systemName: icon).font(DS.sans(30)).foregroundStyle(DS.Ink.p4)
            Text(title).font(DS.serif(16, weight: .semibold)).foregroundStyle(DS.Ink.p2)
            Text(detail).font(DS.sans(12)).foregroundStyle(DS.Ink.p4)
                .multilineTextAlignment(.center).frame(maxWidth: 320)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }

    // MARK: - Actions

    /// Opens the requested note when the index can say where it is; a note
    /// the KB can't resolve opens in Obsidian, as entity chips used to.
    private func openPendingNote() {
        guard let target = pendingNote.wrappedValue else { return }
        switch KBNoteRequest.outcome(target: target, state: service.state, resolve: service.resolveWikilink) {
        case .wait:
            return
        case .open(let path):
            navigate(toPath: path)
        case .fallback:
            if let url = KBNoteRequest.fallbackURL(for: target) { NSWorkspace.shared.open(url) }
        }
        pendingNote.wrappedValue = nil
    }

    /// Select a note by path, expanding its ancestor folders and clearing search.
    private func navigate(toPath path: String) {
        let parts = path.components(separatedBy: "/")
        if parts.count > 1 {
            for i in 1..<parts.count { expanded.insert(parts[0..<i].joined(separator: "/")) }
        }
        selectedPath = path
        searchQuery = ""
        // A "Show on full map" request is spent once you move on to a note.
        mapFocus = nil
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
                // "acme.com" titles "acme.com", not "acme".
                let slug = name.lowercased().hasSuffix(".md") ? String(name.dropLast(3)) : name
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

/// The tree with privacy lock badges. Asks the engine for the labels of the
/// files the tree shows: again after every reparse (an edit may change a label;
/// unchanged files come from the stamp cache) and whenever a folder opens.
private struct KBBadgedTreeView: View {
    @ObservedObject var service: KnowledgeBaseService
    @ObservedObject var privacy: KBPrivacyLabelService
    @Binding var selectedPath: String?
    @Binding var expanded: Set<String>
    @State private var reparses = 0

    private struct RefreshKey: Equatable {
        let paths: [String]
        let reparses: Int
    }

    var body: some View {
        let visible = KBPrivacyLabelService.visibleFiles(in: service.tree, expanded: expanded)
        KBTreeView(nodes: service.tree, selectedPath: $selectedPath, expanded: $expanded,
                   privatePaths: privacy.privatePaths)
            .onReceive(service.$tree) { _ in reparses &+= 1 }
            .task(id: RefreshKey(paths: visible.map(\.relativePath), reparses: reparses)) {
                await privacy.refresh(visible)
            }
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
                    .accessibilityHidden(true)
                Picker("Folder", selection: $directoryPath) {
                    ForEach(directories, id: \.url.path) { dir in
                        Text(dir.label).tag(dir.url.path)
                    }
                }
                .labelsHidden().pickerStyle(.menu)
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("NAME").font(DS.sans(10, weight: .medium)).tracking(0.6).foregroundStyle(DS.Ink.p4)
                    .accessibilityHidden(true)
                TextField("my-note", text: $name)
                    .accessibilityLabel("Name")
                    .textFieldStyle(.roundedBorder).font(DS.sans(13))
                    .onSubmit(create)
                if let problem = KnowledgeBaseFileWriter.noteNameProblem(name) {
                    Text(problem).font(DS.sans(10.5)).foregroundStyle(DS.Status.err)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text(".md is added automatically").font(DS.sans(10.5)).foregroundStyle(DS.Ink.p4)
                }
            }

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Create") { create() }.keyboardShortcut(.defaultAction)
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty
                              || KnowledgeBaseFileWriter.noteNameProblem(name) != nil)
            }
        }
        .padding(20).frame(width: 380)
        .onAppear { directoryPath = defaultDirectory.path }
    }

    private func create() {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, KnowledgeBaseFileWriter.noteNameProblem(trimmed) == nil else { return }
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
