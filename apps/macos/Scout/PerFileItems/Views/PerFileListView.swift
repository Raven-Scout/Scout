// Scout/PerFileItems/Views/PerFileListView.swift
import SwiftUI

/// The Wishlist / Research tab body: header with active-count subtitle, toolbar
/// ＋ Add + Reveal-in-Finder, awaiting items (priority-sorted) + collapsible
/// Resolved section. Mirrors `ProposalsView`'s scroll/header/empty-state structure.
struct PerFileListView: View {
    let config: PerFileTabConfig
    @EnvironmentObject var docService: PerFileDocumentService
    @EnvironmentObject var writerBox: PerFileItemWriterBox
    @EnvironmentObject var appState: AppState

    @State private var resolvedExpanded = false
    @State private var showingAdd = false
    /// The item whose history pane is open. Stored by id and looked up live
    /// from `docService.items`, so the pane never shows a stale snapshot after
    /// Start/Done/Drop or an FSEvent reparse (#43).
    @State private var selectedItemID: String? = nil
    @State private var detailIsFull = false
    /// Bumped after every awaited write, once its commit has landed, so the
    /// open history pane refetches (the FSEvent reparse can beat the commit).
    @State private var historyWriteToken = 0

    private var selectedItem: PerFileItem? {
        selectedItemID.flatMap { id in docService.items.first { $0.id == id } }
    }

    var body: some View {
        ZStack(alignment: .topTrailing) {
            // The list keeps a stable slot so opening the pane doesn't reset
            // its scroll position.
            HStack(alignment: .top, spacing: 0) {
                listScroll.frame(maxWidth: .infinity)
                if !detailIsFull, let item = selectedItem {
                    sideDetail(item)
                        .frame(width: 460)
                        .padding(.vertical, 16).padding(.trailing, 16)
                }
            }
            if detailIsFull, let item = selectedItem {
                fullDetail(item).transition(.opacity)
            }
        }
        .background(
            Group {
                Button("") { closeDetail() }
                    .keyboardShortcut(".", modifiers: .command)
                Button("") { if selectedItemID != nil { detailIsFull.toggle() } }
                    .keyboardShortcut("f", modifiers: [.command, .shift])
            }
            .opacity(0)
            .frame(width: 0, height: 0)
        )
        .animation(.easeInOut(duration: 0.18), value: selectedItemID)
        .animation(.easeInOut(duration: 0.18), value: detailIsFull)
        .onChange(of: docService.items) { _, _ in
            // The file was deleted or renamed out from under the pane.
            if selectedItemID != nil && selectedItem == nil { closeDetail() }
        }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                HStack(spacing: 4) {
                    Button { showingAdd = true } label: {
                        Image(systemName: "plus")
                    }
                    .help("Add a new \(config.addNoun)")
                    Button {
                        NSWorkspace.shared.activateFileViewerSelecting([docService.directoryURL])
                    } label: {
                        Image(systemName: "folder")
                    }
                    .help("Reveal the \(config.title.lowercased()) folder in Finder")
                }
            }
        }
        .sheet(isPresented: $showingAdd) {
            AddItemSheet(config: config, onSubmit: { title, priority, body, optional in
                try await addItem(title: title, priority: priority, body: body, optional: optional)
            }, onCancel: { showingAdd = false })
        }
        .onAppear { docService.load() }
    }

    private var listScroll: some View {
        ScrollView {
            // Deliberately VStack, not LazyVStack (#83, fourth occurrence).
            // Mirrors ProposalsView's swap: the same width-constraining frame
            // chain re-enters the non-convergent height-estimation loop fixed
            // in ActionItemsView (#84) and SectionView (#86), and wishlist /
            // research items are unbounded markdown — PerFileDocumentService
            // parses every .md in the folder with no cap and materializes all
            // of it up front, so laziness buys nothing. The .leading alignment
            // on the maxWidth-920 frame below is now load-bearing: a plain
            // VStack hugs its widest child, where the width-greedy LazyVStack
            // made the horizontal alignment moot.
            VStack(alignment: .leading, spacing: 16) {
                header
                content
            }
            .frame(maxWidth: 920, alignment: .leading)
            .padding(.horizontal, 42)
            .padding(.top, 28)
            .padding(.bottom, 64)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .scrollIndicators(.visible)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(DS.Paper.base)
    }

    // MARK: - History pane (mirrors ControlCenterView's detail panel)

    private func closeDetail() {
        selectedItemID = nil
        detailIsFull = false
    }

    private func historyView(_ item: PerFileItem) -> some View {
        PerFileItemDetailView(item: item, writeToken: historyWriteToken, git: appState.gitService,
                              repoURL: appState.scoutDirectory,
                              sessionLog: appState.sessionLogService)
            .id(item.id)
    }

    private func sideDetail(_ item: PerFileItem) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            detailHeader(item, isExpanded: false)
            EditorialRule()
            historyView(item)
        }
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                .shadow(color: DS.Neumorphic.shadow.opacity(0.4), radius: 8, x: -2, y: 4)
        )
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func fullDetail(_ item: PerFileItem) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            detailHeader(item, isExpanded: true)
            EditorialRule()
            historyView(item)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(DS.Paper.base)
    }

    private func detailHeader(_ item: PerFileItem, isExpanded: Bool) -> some View {
        HStack(alignment: .center, spacing: 10) {
            Button { closeDetail() } label: {
                Image(systemName: "chevron.left").font(.system(size: 12, weight: .medium))
            }
            .buttonStyle(.plainHit).foregroundStyle(DS.Ink.p3).help("Close (⌘.)")
            Text(item.title).font(DS.serif(16, weight: .medium)).foregroundStyle(DS.Ink.p1)
                .lineLimit(1)
            Spacer()
            ItemStatusPill(status: item.status)
            Button { detailIsFull.toggle() } label: {
                Image(systemName: isExpanded
                      ? "arrow.down.right.and.arrow.up.left"
                      : "arrow.up.left.and.arrow.down.right")
                    .font(.system(size: 12)).foregroundStyle(DS.Ink.p3)
            }
            .buttonStyle(.plainHit)
            .help(isExpanded ? "Collapse (⌘⇧F)" : "Expand to full screen (⌘⇧F)")
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 14) {
                Text(config.title)
                    .font(DS.serif(28, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                Spacer(minLength: 0)
            }
            Text(subtitle)
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p3)
        }
        .padding(.bottom, 6)
        .overlay(alignment: .bottom) { EditorialRule() }
    }

    private var subtitle: String {
        let active = docService.activeCount
        switch active {
        case 0:  return "No active \(config.title.lowercased()) items."
        case 1:  return "1 active \(config.addNoun)."
        default: return "\(active) active \(config.title.lowercased()) items."
        }
    }

    // MARK: - Content

    @ViewBuilder
    private var content: some View {
        switch docService.state {
        case .idle, .loading:
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.top, 60)
        case .missing:
            emptyState(
                icon: "tray",
                message: "No \(config.title.lowercased()) folder found yet. Use + to add the first item."
            )
        case .failed(let err):
            Text("Couldn't load \(config.title.lowercased()): \(err)")
                .font(DS.sans(13))
                .foregroundStyle(DS.Status.err)
                .padding(.top, 24)
        case .loaded:
            loadedContent
        }
    }

    @ViewBuilder
    private var loadedContent: some View {
        let awaiting = docService.items.filter(\.isActive).sorted { $0.priority < $1.priority }
        let resolved = docService.items.filter { !$0.isActive }

        if docService.items.isEmpty {
            emptyState(
                icon: "sparkles",
                message: "Nothing here yet. Use + to add a \(config.addNoun)."
            )
        } else {
            if awaiting.isEmpty {
                emptyState(
                    icon: "checkmark.circle",
                    message: "Nothing active right now. Resolved items are below."
                )
            }
            ForEach(awaiting) { item in
                PerFileItemCardView(
                    item: item,
                    optionalLabel: config.optionalField.label,
                    priorityOptions: config.priorities,
                    onChangePriority: { try await changePriority(item, $0) },
                    onChangeStatus: { try await changeStatus(item, $0) },
                    onResolve: { try await resolve(item, $0) },
                    isSelected: selectedItemID == item.id,
                    onShowHistory: { selectedItemID = item.id }
                )
            }
            if !resolved.isEmpty {
                resolvedSection(resolved)
            }
        }
    }

    private func resolvedSection(_ resolved: [PerFileItem]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { resolvedExpanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: resolvedExpanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                    Text("Resolved")
                        .font(DS.sans(11.5, weight: .semibold))
                        .tracking(0.06 * 11.5)
                    Text("\(resolved.count)")
                        .font(DS.mono(11))
                        .foregroundStyle(DS.Ink.p4)
                }
                .foregroundStyle(DS.Ink.p3)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plainHit)

            if resolvedExpanded {
                ForEach(resolved) { item in
                    PerFileItemCardView(
                        item: item,
                        optionalLabel: config.optionalField.label,
                        onChangeStatus: { try await changeStatus(item, $0) },
                        onResolve: { _ in },
                        isSelected: selectedItemID == item.id,
                        onShowHistory: { selectedItemID = item.id }
                    )
                }
            }
        }
        .padding(.top, 12)
    }

    private func emptyState(icon: String, message: String) -> some View {
        VStack(spacing: 14) {
            Image(systemName: icon)
                .font(.largeTitle)
                .foregroundStyle(DS.Ink.p3)
            Text(message)
                .font(DS.serif(14))
                .foregroundStyle(DS.Ink.p2)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 440)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 60)
    }

    // MARK: - Actions

    private func addItem(title: String, priority: ItemPriority, body: String, optional: String?) async throws {
        var source: String?
        var area: String?
        switch config.optionalField {
        case .none: break
        case .source: source = optional
        case .area: area = optional
        }
        _ = try await writerBox.writer.addItem(
            title: title, priority: priority, body: body,
            source: source, area: area,
            in: docService.directoryURL, noun: config.addNoun
        )
        showingAdd = false
        docService.reload()
        historyWriteToken += 1
    }

    private func resolve(_ item: PerFileItem, _ resolution: ItemResolution) async throws {
        try await writerBox.writer.resolve(resolution, fileURL: item.fileURL, label: item.title)
        docService.reload()
        historyWriteToken += 1
    }

    private func changePriority(_ item: PerFileItem, _ priority: ItemPriority) async throws {
        try await writerBox.writer.setPriority(priority, fileURL: item.fileURL, label: item.title)
        docService.reload()
        historyWriteToken += 1
    }

    private func changeStatus(_ item: PerFileItem, _ status: ItemStatus) async throws {
        try await writerBox.writer.setStatus(status, fileURL: item.fileURL, label: item.title)
        docService.reload()
        historyWriteToken += 1
    }
}
