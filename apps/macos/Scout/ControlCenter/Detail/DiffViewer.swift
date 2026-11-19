import SwiftUI
import AppKit

struct DiffViewer: View {
    let commits: [Commit]
    @EnvironmentObject var state: AppState

    @State private var expanded: Set<String> = []
    /// Each loaded commit's diff, split by `TextChunks`.
    @State private var diffChunks: [String: [String]] = [:]
    @State private var ghosttyFailure: LaunchFailure?

    var body: some View {
        VStack(alignment: .leading) {
            if commits.isEmpty {
                Text("No commits in this run.").foregroundStyle(.secondary)
            } else {
                HStack {
                    Spacer()
                    Button("Open full diff in Ghostty") { openInGhostty() }
                    Button("Copy diff") {
                        Task { await copyAll() }
                    }
                }
                .padding(.horizontal, 4)
                List {
                    ForEach(commits) { c in
                        DisclosureGroup(
                            isExpanded: Binding(
                                get: { expanded.contains(c.id) },
                                set: { v in
                                    if v {
                                        expanded.insert(c.id)
                                        Task { await loadDiff(c.id) }
                                    } else {
                                        expanded.remove(c.id)
                                    }
                                }
                            )
                        ) {
                            // One List row per chunk of lines, so the list lays a big
                            // diff out as it scrolls in, not as one huge row (#25).
                            if let chunks = diffChunks[c.id] {
                                ForEach(Array(chunks.enumerated()), id: \.offset) { _, chunk in
                                    Text(chunk)
                                        .font(DS.mono(10))
                                        .textSelection(.enabled)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                        // Chunks read as one diff: no rule or gap between them.
                                        .listRowSeparator(.hidden)
                                        .listRowInsets(EdgeInsets(top: 0, leading: 8, bottom: 0, trailing: 8))
                                }
                            } else {
                                Text("Loading…")
                                    .font(DS.mono(10))
                                    .foregroundStyle(.secondary)
                                    .padding(.vertical, 4)
                            }
                        } label: {
                            VStack(alignment: .leading) {
                                Text(c.subject)
                                Text("\(c.shortSHA) · \(c.filesChanged) files · +\(c.insertions) −\(c.deletions)")
                                    .font(DS.sans(10))
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
        }
        .launchFailureAlert("Couldn't open the diff in Ghostty", failure: $ghosttyFailure)
    }

    private func openInGhostty() {
        guard let first = commits.last, let last = commits.first else { return }
        let folder = state.scoutDirectory
        Task {
            do {
                try await GhosttyLauncher.openNewTab(
                    cwd: folder,
                    runningCommand: "git diff \(first.id)^..\(last.id)"
                )
            } catch {
                ghosttyFailure = LaunchFailure(error)
            }
        }
    }

    private func loadDiff(_ sha: String) async {
        do {
            let d = try await state.gitService.diff(from: "\(sha)^", to: sha)
            let chunks = TextChunks.split(text: d)
            await MainActor.run { diffChunks[sha] = chunks.isEmpty ? ["(no changes)"] : chunks }
        } catch {
            await MainActor.run {
                diffChunks[sha] = ["Couldn't load this diff: \(error.localizedDescription)"]
            }
        }
    }

    private func copyAll() async {
        guard let first = commits.last, let last = commits.first else { return }
        do {
            let d = try await state.gitService.diff(from: "\(first.id)^", to: last.id)
            await MainActor.run {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(d, forType: .string)
            }
        } catch { /* ignore */ }
    }
}
