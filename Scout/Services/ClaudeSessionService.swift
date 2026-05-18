import Foundation

/// Parsed tool-use telemetry for a single Scout run, lifted from the
/// claude-code session JSONL the runner script produces. Surfaces tool
/// counts + per-call inputs to the Tool / Files run-detail tabs.
///
/// Why JSONL: the shell-side run log is unstructured text, but the
/// claude-code session next to it captures every Bash/Read/Edit/Write call
/// as structured JSON. Reading that gives us file activity and tool usage
/// without depending on log scraping.
struct ClaudeSessionActivity: Equatable, Sendable {
    struct ToolCall: Equatable, Sendable, Identifiable {
        let id: String
        let name: String
        let timestamp: Date?
        let summary: String        // a short one-line render of inputs
        let filePath: String?      // for Read / Edit / Write / NotebookEdit
        let isError: Bool
    }

    let sessionId: String
    let customTitle: String?
    let firstTimestamp: Date?
    let calls: [ToolCall]

    var byTool: [(name: String, count: Int)] {
        var bucket: [String: Int] = [:]
        for c in calls { bucket[c.name, default: 0] += 1 }
        return bucket.sorted { $0.value > $1.value }.map { ($0.key, $0.value) }
    }

    var filesRead: [String] {
        Array(Set(calls.filter { $0.name == "Read" }.compactMap(\.filePath))).sorted()
    }
    var filesEdited: [String] {
        Array(Set(calls.filter { $0.name == "Edit" || $0.name == "NotebookEdit" }
            .compactMap(\.filePath))).sorted()
    }
    var filesWritten: [String] {
        Array(Set(calls.filter { $0.name == "Write" }.compactMap(\.filePath))).sorted()
    }
}

actor ClaudeSessionService {
    private let projectsDirectory: URL
    private var cache: [URL: ClaudeSessionActivity] = [:]

    init(projectsDirectory: URL) {
        self.projectsDirectory = projectsDirectory
    }

    /// Default location for Scout's claude-code project: `~/.claude/projects/-Users-<user>-Scout`.
    /// Resolves the encoded directory name from `~/Scout` so this still works
    /// if the username changes.
    static func defaultScoutSessionsDirectory(scoutDirectory: URL) -> URL {
        let encoded = scoutDirectory.path
            .replacingOccurrences(of: "/", with: "-")
        let home = FileManager.default.homeDirectoryForCurrentUser
        return home
            .appendingPathComponent(".claude/projects")
            .appendingPathComponent(encoded)
    }

    /// Find the claude-code session that ran the given `Run`. Match on the
    /// `customTitle` time fragment first (HHmm), then fall back to whichever
    /// session's first timestamp is closest within ±10 minutes.
    func activity(for run: Run) async -> ClaudeSessionActivity? {
        let fm = FileManager.default
        guard let urls = try? fm.contentsOfDirectory(
            at: projectsDirectory,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else { return nil }

        let jsonls = urls.filter { $0.pathExtension == "jsonl" }
        // Recent first — Scout runs are short and we usually want the latest
        // matching session.
        let sorted = jsonls.sorted { a, b in
            (try? a.resourceValues(forKeys: [.contentModificationDateKey])
                .contentModificationDate)
            ?? .distantPast
            >
            (try? b.resourceValues(forKeys: [.contentModificationDateKey])
                .contentModificationDate)
            ?? .distantPast
        }

        // Build the title fragment we expect: scout-<mode>-YYYYMMDD-HHMM.
        // Different runs (briefing vs dreaming vs research) embed different
        // mode strings, so match on the date+time tail rather than the whole.
        let dateFmt = DateFormatter()
        dateFmt.dateFormat = "yyyyMMdd-HHmm"
        let target = dateFmt.string(from: run.startedAt)

        for url in sorted {
            if let cached = cache[url] {
                if (cached.customTitle ?? "").hasSuffix(target) {
                    return cached
                }
                continue
            }
            if let activity = try? Self.parse(url: url),
               (activity.customTitle ?? "").hasSuffix(target) {
                cache[url] = activity
                return activity
            }
        }

        // Fall back: the closest session within 10 minutes of run.startedAt.
        var best: (ClaudeSessionActivity, TimeInterval)? = nil
        for url in sorted.prefix(40) {
            let activity: ClaudeSessionActivity
            if let cached = cache[url] {
                activity = cached
            } else if let parsed = try? Self.parse(url: url) {
                cache[url] = parsed
                activity = parsed
            } else {
                continue
            }
            guard let ts = activity.firstTimestamp else { continue }
            let delta = abs(ts.timeIntervalSince(run.startedAt))
            if delta < 600 && (best == nil || delta < best!.1) {
                best = (activity, delta)
            }
        }
        return best?.0
    }

    /// Aggregate tool-use counts across multiple runs. Used by the Usage rail
    /// card to surface "today's" tool calls, file edits, bash invocations,
    /// etc. — stats that previously lived only inside the per-run Tools tab.
    ///
    /// CC-6: extends the Usage card past the bare token totals so the user
    /// can see *what kind of work* Scout did today, not just how much
    /// inference it ran.
    func aggregateStats(for runs: [Run]) async -> AggregateStats {
        var totalCalls = 0
        var byTool: [String: Int] = [:]
        var filesEdited: Set<String> = []
        var filesWritten: Set<String> = []
        var filesRead: Set<String> = []
        for run in runs {
            guard let a = await activity(for: run) else { continue }
            totalCalls += a.calls.count
            for c in a.calls { byTool[c.name, default: 0] += 1 }
            for f in a.filesEdited  { filesEdited.insert(f) }
            for f in a.filesWritten { filesWritten.insert(f) }
            for f in a.filesRead    { filesRead.insert(f) }
        }
        return AggregateStats(
            totalToolCalls: totalCalls,
            byTool: byTool,
            uniqueFilesEdited: filesEdited.count,
            uniqueFilesWritten: filesWritten.count,
            uniqueFilesRead: filesRead.count
        )
    }

    struct AggregateStats: Equatable, Sendable {
        var totalToolCalls: Int
        var byTool: [String: Int]
        var uniqueFilesEdited: Int
        var uniqueFilesWritten: Int
        var uniqueFilesRead: Int

        var bashCalls: Int   { byTool["Bash"] ?? 0 }
        var webFetches: Int  { byTool["WebFetch"] ?? 0 }
        var webSearches: Int { byTool["WebSearch"] ?? 0 }

        /// Total file mutations (edits + writes — same path counted twice if
        /// it appears in both sets, intentional).
        var fileMutations: Int { uniqueFilesEdited + uniqueFilesWritten }

        /// Top 3 tools by call count for the compact "today" summary.
        var topTools: [(name: String, count: Int)] {
            byTool.sorted { $0.value > $1.value }.prefix(3).map { (name: $0.key, count: $0.value) }
        }
    }

    // MARK: - Parsing

    private nonisolated static func parse(url: URL) throws -> ClaudeSessionActivity {
        let data = try Data(contentsOf: url)
        let text = String(data: data, encoding: .utf8) ?? ""
        var sessionId: String = url.deletingPathExtension().lastPathComponent
        var customTitle: String? = nil
        var firstTimestamp: Date? = nil
        var calls: [ClaudeSessionActivity.ToolCall] = []

        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let isoNoFrac = ISO8601DateFormatter()
        isoNoFrac.formatOptions = [.withInternetDateTime]

        // tool_use_id → toolUseId mapping for tool_result error flagging on a
        // second pass would be ideal, but it's expensive on big sessions.
        // For now flag errors only when the assistant message itself says so.

        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            guard let lineData = line.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any]
            else { continue }

            if let title = obj["customTitle"] as? String { customTitle = title }
            if let sid = obj["sessionId"] as? String { sessionId = sid }
            if let tsStr = obj["timestamp"] as? String, firstTimestamp == nil {
                firstTimestamp = iso.date(from: tsStr) ?? isoNoFrac.date(from: tsStr)
            }

            // Both queue entries and assistant turns can carry a `message`
            // dict whose `content` is an array of blocks. Tool calls live
            // inside `tool_use` blocks within that array.
            guard let message = obj["message"] as? [String: Any],
                  let content = message["content"] as? [[String: Any]]
            else { continue }

            let ts: Date? = (obj["timestamp"] as? String).flatMap {
                iso.date(from: $0) ?? isoNoFrac.date(from: $0)
            }

            for block in content where (block["type"] as? String) == "tool_use" {
                guard let name = block["name"] as? String,
                      let id = block["id"] as? String
                else { continue }
                let input = (block["input"] as? [String: Any]) ?? [:]
                let summary = summarize(name: name, input: input)
                let filePath = (input["file_path"] as? String)
                    ?? (input["path"] as? String)
                    ?? (input["notebook_path"] as? String)
                calls.append(ClaudeSessionActivity.ToolCall(
                    id: id,
                    name: name,
                    timestamp: ts,
                    summary: summary,
                    filePath: filePath,
                    isError: false
                ))
            }
        }

        return ClaudeSessionActivity(
            sessionId: sessionId,
            customTitle: customTitle,
            firstTimestamp: firstTimestamp,
            calls: calls
        )
    }

    private nonisolated static func summarize(name: String, input: [String: Any]) -> String {
        switch name {
        case "Bash":
            let cmd = (input["command"] as? String) ?? ""
            return cmd
        case "Read":
            return (input["file_path"] as? String) ?? "?"
        case "Edit", "Write":
            let path = (input["file_path"] as? String) ?? "?"
            return path
        case "Glob":
            return (input["pattern"] as? String) ?? "?"
        case "Grep":
            let pattern = (input["pattern"] as? String) ?? "?"
            let path = (input["path"] as? String).map { " in \($0)" } ?? ""
            return "\(pattern)\(path)"
        case "WebFetch":
            return (input["url"] as? String) ?? "?"
        case "WebSearch":
            return (input["query"] as? String) ?? "?"
        case "TodoWrite":
            if let todos = input["todos"] as? [[String: Any]] {
                return "\(todos.count) todo\(todos.count == 1 ? "" : "s")"
            }
            return ""
        case "ToolSearch":
            return (input["query"] as? String) ?? "?"
        default:
            // Best-effort: pick the first short string field.
            for (_, v) in input {
                if let s = v as? String, s.count < 200 { return s }
            }
            return ""
        }
    }
}
