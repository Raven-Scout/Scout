import SwiftUI
import AppKit

struct DiffViewer: View {
    let commits: [Commit]
    @EnvironmentObject var state: AppState

    @State private var expanded: Set<String> = []
    @State private var diffs: [String: String] = [:]

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
                            Text(diffs[c.id] ?? "Loading…")
                                .font(.system(.caption, design: .monospaced))
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.vertical, 4)
                        } label: {
                            VStack(alignment: .leading) {
                                Text(c.subject)
                                Text("\(c.shortSHA) · \(c.filesChanged) files · +\(c.insertions) −\(c.deletions)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
        }
    }

    private func openInGhostty() {
        guard let first = commits.last, let last = commits.first else { return }
        GhosttyLauncher.openNewTab(
            cwd: FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Scout"),
            runningCommand: "git diff \(first.id)^..\(last.id)"
        )
    }

    private func loadDiff(_ sha: String) async {
        do {
            let d = try await state.gitService.diff(from: "\(sha)^", to: sha)
            await MainActor.run { diffs[sha] = d }
        } catch {
            await MainActor.run {
                diffs[sha] = "Error loading diff: \(error.localizedDescription)"
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
