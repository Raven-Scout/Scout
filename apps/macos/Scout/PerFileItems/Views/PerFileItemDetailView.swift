import SwiftUI

/// The Wishlist/Research detail pane body (#43): an outcome line for resolved
/// items, then every commit that touched the item's file, newest first. Each
/// row expands to this file's patch, the commit's other files, and (when
/// linked) a jump to the run in Control Center.
struct PerFileItemDetailView: View {
    let item: PerFileItem
    @ObservedObject var sessionLog: SessionLogService
    @StateObject private var model: PerFileItemActivityModel
    @EnvironmentObject private var appState: AppState
    @State private var expanded: Set<String> = []

    init(item: PerFileItem, git: GitService, repoURL: URL, sessionLog: SessionLogService) {
        self.item = item
        _sessionLog = ObservedObject(wrappedValue: sessionLog)
        _model = StateObject(wrappedValue: PerFileItemActivityModel(git: git, repoURL: repoURL))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                content
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // PerFileItem is Equatable: FSEvent republishes with identical content
        // don't refetch; a status/body change does.
        .task(id: item) { await model.load(item) }
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
        case .unavailable(let message):
            Text(message).font(DS.serif(14)).foregroundStyle(DS.Ink.p3).padding(.top, 24)
        case .failed(let message):
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .font(DS.sans(12)).foregroundStyle(DS.Status.err)
        case .loaded(let revisions):
            let entries = ItemActivity.entries(revisions: revisions, runs: sessionLog.runs,
                                               status: item.status, now: Date())
            if let outcome = ItemActivity.outcome(entries: entries, status: item.status) {
                Text(outcome.summary(date: Self.dayText))
                    .font(DS.sans(12.5, weight: .semibold)).foregroundStyle(DS.Ink.p1)
            }
            if entries.isEmpty {
                Text("No activity yet — this item hasn't been committed.")
                    .font(DS.serif(14)).foregroundStyle(DS.Ink.p3).padding(.top, 24)
            } else {
                ForEach(entries) { row($0) }
            }
        }
    }

    // MARK: - Timeline row

    @ViewBuilder
    private func row(_ entry: ItemActivityEntry) -> some View {
        let isOpen = expanded.contains(entry.id)
        VStack(alignment: .leading, spacing: 8) {
            Button { toggle(entry.id) } label: {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: isOpen ? "chevron.down" : "chevron.right")
                        .font(.system(size: 9, weight: .semibold)).foregroundStyle(DS.Ink.p4)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(entry.source.label).font(DS.sans(11, weight: .semibold))
                                .foregroundStyle(DS.Ink.p3)
                            Text(Self.stampText(entry.commit.timestamp)).font(DS.mono(10))
                                .foregroundStyle(DS.Ink.p4)
                            if entry.isResolving {
                                Text("outcome").font(DS.mono(10)).foregroundStyle(DS.Status.ok)
                            }
                        }
                        Text(entry.commit.subject).font(DS.serif(13)).foregroundStyle(DS.Ink.p1)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("\(entry.commit.shortSHA)  +\(entry.commit.insertions) −\(entry.commit.deletions)")
                            .font(DS.mono(10)).foregroundStyle(DS.Ink.p4)
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plainHit)

            if isOpen { expandedBody(entry) }
        }
        .padding(.vertical, 6)
        .overlay(alignment: .bottom) { EditorialRule() }
    }

    @ViewBuilder
    private func expandedBody(_ entry: ItemActivityEntry) -> some View {
        if entry.revision.patch.isEmpty {
            Text("No change to this file in this commit.")
                .font(DS.sans(11)).foregroundStyle(DS.Ink.p3)
        } else {
            CommitDiffView(patch: entry.revision.patch)
        }
        otherFilesSection(entry.commit)
        if let run = entry.source.run {
            Button { appState.requestOpenRun(run.id) } label: {
                Label("Open run in Control Center", systemImage: "arrow.up.right.square")
                    .font(DS.sans(11, weight: .medium))
            }
            .buttonStyle(.plainHit).foregroundStyle(DS.Ink.p2)
        }
    }

    @ViewBuilder
    private func otherFilesSection(_ commit: Commit) -> some View {
        switch model.otherFiles[commit.id] {
        case .loaded(let files) where files.isEmpty:
            EmptyView()
        case .loaded(let files):
            VStack(alignment: .leading, spacing: 2) {
                Text("Also changed in this commit (\(files.count))")
                    .font(DS.sans(10.5, weight: .semibold)).foregroundStyle(DS.Ink.p3)
                ForEach(files.prefix(20), id: \.self) { path in
                    Text(path).font(DS.mono(10)).foregroundStyle(DS.Ink.p3).lineLimit(1)
                }
                if files.count > 20 {
                    Text("+\(files.count - 20) more").font(DS.mono(10)).foregroundStyle(DS.Ink.p4)
                }
            }
        case .failed(let message):
            Text(message).font(DS.sans(10.5)).foregroundStyle(DS.Status.err)
        case .loading, nil:
            ProgressView().controlSize(.small)
                .task { await model.loadOtherFiles(for: commit) }
        }
    }

    // MARK: - Helpers

    private func toggle(_ id: String) {
        if expanded.contains(id) { expanded.remove(id) } else { expanded.insert(id) }
    }

    private static let dayFormatter: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MMM d"; return f
    }()
    private static let stampFormatter: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MMM d, HH:mm"; return f
    }()
    private static func dayText(_ d: Date) -> String { dayFormatter.string(from: d) }
    private static func stampText(_ d: Date) -> String { stampFormatter.string(from: d) }
}
