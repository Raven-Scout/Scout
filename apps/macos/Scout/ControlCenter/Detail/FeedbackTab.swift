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
    /// The body as loaded for this run. Saving only happens when the text
    /// differs, so opening a note (or switching runs) never rewrites it.
    @State private var loadedText: String = ""
    /// The note exists but couldn't be read. Editing is off: saving an empty
    /// or retyped body would delete or replace the note.
    @State private var isUnreadable = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("Notes for this run")
                    .font(DS.sans(13, weight: .bold))
                Spacer()
                if let s = savedAt {
                    Text("Saved \(s.formatted(.relative(presentation: .named)))")
                        .font(DS.sans(10))
                        .foregroundStyle(.secondary)
                } else if isUnreadable {
                    Text("Couldn't read the saved note")
                        .font(DS.sans(10))
                        .foregroundStyle(DS.Status.err)
                } else if let err = saveError {
                    Text("Error: \(err)")
                        .font(DS.sans(10))
                        .foregroundStyle(DS.Status.err)
                }
            }
            Text("Like a Slack reply — leave a note about what worked, what didn't, or what you'd want next time. Markdown is fine.")
                .font(DS.sans(10))
                .foregroundStyle(.secondary)

            TextEditor(text: $text)
                .font(DS.sans(13))
                .frame(minHeight: 220)
                .padding(6)
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(Color.secondary.opacity(0.3))
                )
                .disabled(isUnreadable)
                .onChange(of: text) { _, new in
                    guard Self.shouldSave(text: new, loadedText: loadedText, isUnreadable: isUnreadable) else { return }
                    saveTask?.cancel()
                    // Capture the target now: if the user switches runs before
                    // the debounce fires, this edit still lands in its own note.
                    let target = SaveTarget(url: feedbackURL, run: run, body: new)
                    saveTask = Task { await debouncedSave(target) }
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
                Button("Save") {
                    guard Self.shouldSave(text: text, loadedText: loadedText, isUnreadable: isUnreadable) else { return }
                    saveTask?.cancel()
                    save(SaveTarget(url: feedbackURL, run: run, body: text))
                }
                .keyboardShortcut("s", modifiers: [.command])
                .disabled(isUnreadable)
            }
        }
        .padding(16)
        .task(id: run.id) { load() }
    }

    private var feedbackURL: URL {
        state.scoutDirectory
            .appendingPathComponent(".scout-feedback")
            .appendingPathComponent("\(Self.slug(for: run)).md")
    }

    enum LoadOutcome: Equatable {
        case missing
        case loaded(String)
        case unreadable
    }

    /// A note that exists but can't be read (I/O error, not UTF-8) is
    /// `.unreadable`, never an empty body.
    static func loadOutcome(data: Data?, fileExists: Bool) -> LoadOutcome {
        guard let data else { return fileExists ? .unreadable : .missing }
        guard let s = String(data: data, encoding: .utf8) else { return .unreadable }
        return .loaded(stripFrontmatter(s))
    }

    static func shouldSave(text: String, loadedText: String, isUnreadable: Bool) -> Bool {
        !isUnreadable && text != loadedText
    }

    private struct SaveTarget {
        let url: URL
        let run: Run
        let body: String
    }

    private func load() {
        let url = feedbackURL
        let outcome = Self.loadOutcome(
            data: try? Data(contentsOf: url),
            fileExists: FileManager.default.fileExists(atPath: url.path))
        didLoad = true
        saveError = nil
        switch outcome {
        case .loaded(let body):
            // Frontmatter was stripped so the user only edits the body. We
            // re-add it on save so the file stays self-describing.
            isUnreadable = false
            loadedText = body
            text = body
            savedAt = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        case .missing:
            isUnreadable = false
            loadedText = ""
            text = ""
            savedAt = nil
        case .unreadable:
            isUnreadable = true
            loadedText = ""
            text = ""
            savedAt = nil
        }
    }

    private func debouncedSave(_ target: SaveTarget) async {
        do {
            try await Task.sleep(nanoseconds: 600_000_000)
        } catch {
            return // cancelled by next keystroke
        }
        save(target)
    }

    private func save(_ target: SaveTarget) {
        let body = target.body.trimmingCharacters(in: .whitespacesAndNewlines)
        let url = target.url
        // A debounced save can land after the user switched runs: it still
        // writes its own note, but only the visible run's status changes.
        let isCurrent = url == feedbackURL
        do {
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            if body.isEmpty {
                // No content: remove any prior file so we don't ship stale
                // empty notes back through downstream consumers.
                if FileManager.default.fileExists(atPath: url.path) {
                    try FileManager.default.removeItem(at: url)
                }
            } else {
                try makeFile(body: body, run: target.run)
                    .write(to: url, atomically: true, encoding: .utf8)
            }
            if isCurrent {
                savedAt = Date()
                saveError = nil
                loadedText = target.body
            }
        } catch {
            if isCurrent { saveError = error.localizedDescription }
        }
    }

    /// The feedback file's name: `<yyyy-MM-dd_HH-mm>_<type>`, with the log's
    /// `-SS-PID` after the minute for a later run in the same minute.
    static func slug(for run: Run) -> String {
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd_HH-mm"
        // A later run in the same minute (seconds+PID log name) gets its own
        // file; a minute-named run keeps the name its notes always had.
        let suffix = SessionLogService.parseFilename(run.logPath)?.uniqueSuffix ?? ""
        return "\(fmt.string(from: run.startedAt))\(suffix)_\(run.type.rawValue)"
    }

    private func makeFile(body: String, run: Run) -> String {
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

    static func stripFrontmatter(_ s: String) -> String {
        guard s.hasPrefix("---\n") else { return s }
        let after = s.dropFirst(4)
        guard let endRange = after.range(of: "\n---\n") else { return s }
        return String(after[endRange.upperBound...]).trimmingCharacters(in: .newlines)
    }
}
