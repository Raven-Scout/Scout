import SwiftUI
import AppKit

/// Per-run inventory of the files Scout touched, grouped by operation
/// (read / edited / written). Reads from the matching claude-code session
/// JSONL — that's the only place tool-level file activity is captured.
struct FilesTab: View {
    let run: Run
    @EnvironmentObject var state: AppState
    @State private var activity: ClaudeSessionActivity? = nil
    @State private var didLoad = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if !didLoad {
                    Text("Loading…").foregroundStyle(.secondary).padding()
                } else if let a = activity {
                    section("Edited", paths: a.filesEdited, color: DS.Accent.ink)
                    section("Created", paths: a.filesWritten, color: DS.Status.ok)
                    section("Read", paths: a.filesRead, color: DS.Ink.p2)
                    if a.filesEdited.isEmpty && a.filesWritten.isEmpty && a.filesRead.isEmpty {
                        Text("No file activity recorded for this run.")
                            .foregroundStyle(.secondary)
                            .italic()
                    }
                } else {
                    Text("No matching claude-code session found.")
                        .foregroundStyle(.secondary)
                        .italic()
                    Text("Scout writes session files to ~/.claude/projects/-Users-…-Scout — they appear once a run starts.")
                        .font(DS.font(.caption2))
                        .foregroundStyle(.secondary)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task(id: run.id) {
            activity = await state.claudeSessionService.activity(for: run)
            didLoad = true
        }
    }

    @ViewBuilder
    private func section(_ title: String, paths: [String], color: Color) -> some View {
        if !paths.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Circle().fill(color).frame(width: 8, height: 8)
                    Text(title.uppercased())
                        .font(DS.font(.caption2, .mono))
                        .fontWeight(.medium)
                        .foregroundStyle(.secondary)
                    Text("\(paths.count)")
                        .font(DS.font(.caption2, .mono))
                        .foregroundStyle(.secondary)
                }
                ForEach(paths, id: \.self) { path in
                    fileRow(path)
                }
            }
        }
    }

    private func fileRow(_ path: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "doc.text")
                .foregroundStyle(.secondary)
                .font(DS.font(.caption2))
            Text(displayPath(path))
                .font(DS.font(.label, .mono))
                .lineLimit(1)
                .truncationMode(.middle)
                .help(path)
            Spacer()
            Button {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
            } label: {
                Image(systemName: "arrow.up.right.square")
            }
            .buttonStyle(.plainHit)
            .foregroundStyle(.secondary)
            .help("Reveal in Finder")
            .accessibilityLabel("Reveal in Finder")
        }
        .padding(.vertical, 2)
    }

    private func displayPath(_ p: String) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return p.hasPrefix(home) ? "~" + p.dropFirst(home.count) : p
    }
}
