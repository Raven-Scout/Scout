import SwiftUI

/// Per-run feedback. Mirrors the Slack-thread reply pattern: the user can
/// jot what they thought of the run while it's fresh and Scout's later
/// passes can pick the notes up. Persists to ~/Scout/.scout-feedback/<runId>.md
/// so other tools (cron, future MCP server) can read it.
struct FeedbackTab: View {
    let run: Run
    @EnvironmentObject var state: AppState
    @State private var text: String = ""
    @State private var savedAt: Date? = nil
    @State private var saveError: String? = nil
    @State private var didLoad = false
    @State private var saveTask: Task<Void, Never>? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("Notes for this run")
                    .font(.headline)
                Spacer()
                if let s = savedAt {
                    Text("Saved \(s.formatted(.relative(presentation: .named)))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else if let err = saveError {
                    Text("Error: \(err)")
                        .font(.caption)
                        .foregroundStyle(.red)
                }
            }
            Text("Like a Slack reply — leave a note about what worked, what didn't, or what you'd want next time. Markdown is fine.")
                .font(.caption)
                .foregroundStyle(.secondary)

            TextEditor(text: $text)
                .font(.system(.body, design: .default))
                .frame(minHeight: 220)
                .padding(6)
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(Color.secondary.opacity(0.3))
                )
                .onChange(of: text) { _, _ in
                    saveTask?.cancel()
                    saveTask = Task { await debouncedSave() }
                }

            HStack {
                Button("Open feedback file") {
                    NSWorkspace.shared.open(feedbackURL)
                }
                .disabled(text.isEmpty && savedAt == nil)
                Button("Reveal in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([feedbackURL])
                }
                .disabled(text.isEmpty && savedAt == nil)
                Spacer()
                Button("Save") { Task { save() } }
                    .keyboardShortcut("s", modifiers: [.command])
            }
        }
        .padding(16)
        .task(id: run.id) { load() }
    }

    private var feedbackURL: URL {
        state.scoutDirectory
            .appendingPathComponent(".scout-feedback")
            .appendingPathComponent("\(slug(for: run)).md")
    }

    private func load() {
        let url = feedbackURL
        if let data = try? Data(contentsOf: url),
           let s = String(data: data, encoding: .utf8) {
            // Strip the YAML frontmatter we wrote so the user only edits the
            // body. We re-add it on save so the file stays self-describing.
            text = stripFrontmatter(s)
            didLoad = true
            savedAt = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        } else {
            text = ""
            didLoad = true
            savedAt = nil
        }
    }

    private func debouncedSave() async {
        do {
            try await Task.sleep(nanoseconds: 600_000_000)
        } catch {
            return // cancelled by next keystroke
        }
        save()
    }

    private func save() {
        let body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let url = feedbackURL
        let dir = url.deletingLastPathComponent()
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            if body.isEmpty {
                // No content: remove any prior file so we don't ship stale
                // empty notes back through downstream consumers.
                if FileManager.default.fileExists(atPath: url.path) {
                    try FileManager.default.removeItem(at: url)
                }
                savedAt = Date()
                saveError = nil
                return
            }
            let content = makeFile(body: body)
            try content.write(to: url, atomically: true, encoding: .utf8)
            savedAt = Date()
            saveError = nil
        } catch {
            saveError = error.localizedDescription
        }
    }

    private func slug(for run: Run) -> String {
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd_HH-mm"
        return "\(fmt.string(from: run.startedAt))_\(run.type.rawValue)"
    }

    private func makeFile(body: String) -> String {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        var out = "---\n"
        out += "run_id: \(run.id)\n"
        out += "type: \(run.type.rawValue)\n"
        out += "started_at: \(iso.string(from: run.startedAt))\n"
        out += "status: \(run.status.rawValue)\n"
        out += "feedback_updated: \(iso.string(from: Date()))\n"
        out += "---\n\n"
        out += body + "\n"
        return out
    }

    private func stripFrontmatter(_ s: String) -> String {
        guard s.hasPrefix("---\n") else { return s }
        let after = s.dropFirst(4)
        guard let endRange = after.range(of: "\n---\n") else { return s }
        return String(after[endRange.upperBound...]).trimmingCharacters(in: .newlines)
    }
}
