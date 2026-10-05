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

/// Reads transcript bytes. Injected so tests can assert *how much* the
/// matcher actually reads: finding which session belongs to a run must not
/// pull whole transcripts off disk (the archive runs to hundreds of MB).
protocol TranscriptByteReading: Sendable {
    /// Up to `maxBytes` from the start of the file, or the whole file when
    /// `maxBytes` is nil.
    ///
    /// `nonisolated`: the parse paths that call this run off the main actor
    /// (`parse`/`parseHead` are `nonisolated static`, reached from detached
    /// work), so the project's default main-actor isolation must not apply.
    nonisolated func read(_ url: URL, maxBytes: Int?) throws -> Data
}

struct FileSystemTranscriptReader: TranscriptByteReading {
    nonisolated init() {}

    nonisolated func read(_ url: URL, maxBytes: Int?) throws -> Data {
        guard let maxBytes else { return try Data(contentsOf: url) }
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        return try handle.read(upToCount: maxBytes) ?? Data()
    }
}

actor ClaudeSessionService {
    private let projectsDirectory: URL
    private let reader: any TranscriptByteReading
    private var cache: [URL: ClaudeSessionActivity] = [:]
    /// Head-only parses, keyed by transcript. Far smaller than `cache` — this
    /// is what makes repeat lookups cheap without holding every tool call.
    private var heads: [URL: SessionHead] = [:]

    init(
        projectsDirectory: URL,
        reader: any TranscriptByteReading = FileSystemTranscriptReader()
    ) {
        self.projectsDirectory = projectsDirectory
        self.reader = reader
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
        let sorted = sortedTranscripts()

        // Build the title fragment we expect: scout-<mode>-YYYYMMDD-HHMM.
        // Different runs (briefing vs dreaming vs research) embed different
        // mode strings, so match on the date+time tail rather than the whole.
        let dateFmt = DateFormatter()
        dateFmt.dateFormat = "yyyyMMdd-HHmm"
        let target = dateFmt.string(from: run.startedAt)

        // Pass 1 — title match. Reads only each transcript's head: deciding
        // *which* session ran a job never needs its tool calls, and pulling
        // whole files here is what made the Usage card cost hundreds of MB.
        for url in sorted {
            guard let head = head(of: url),
                  (head.customTitle ?? "").hasSuffix(target)
            else { continue }
            if let full = fullActivity(at: url) { return full }
        }

        // Pass 2 — fall back to the session starting closest to the run,
        // within 10 minutes. Still heads only; just the winner is parsed.
        var best: (url: URL, delta: TimeInterval)? = nil
        for url in sorted.prefix(40) {
            guard let ts = head(of: url)?.firstTimestamp else { continue }
            let delta = abs(ts.timeIntervalSince(run.startedAt))
            if delta < 600 && (best == nil || delta < best!.delta) {
                best = (url, delta)
            }
        }
        guard let winner = best?.url else { return nil }
        return fullActivity(at: winner)
    }

    /// Transcripts newest-first — Scout runs are short and we usually want the
    /// latest matching session. Decorate-sort-undecorate so each file is
    /// stat'd once; calling `resourceValues` inside the comparator re-stat'd
    /// it O(n log n) times on every lookup.
    private func sortedTranscripts() -> [URL] {
        guard let urls = try? FileManager.default.contentsOfDirectory(
            at: projectsDirectory,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }
        return urls
            .filter { $0.pathExtension == "jsonl" }
            .map { url in
                (url, (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?
                    .contentModificationDate ?? .distantPast)
            }
            .sorted { $0.1 > $1.1 }
            .map(\.0)
    }

    /// Fully-parsed activity for one transcript, memoised. Only ever called
    /// for a transcript that already matched on its head.
    private func fullActivity(at url: URL) -> ClaudeSessionActivity? {
        if let cached = cache[url] { return cached }
        guard let parsed = try? Self.parse(url: url, reader: reader) else { return nil }
        cache[url] = parsed
        return parsed
    }

    private func head(of url: URL) -> SessionHead? {
        if let cached = heads[url] { return cached }
        guard let parsed = Self.parseHead(url: url, reader: reader) else { return nil }
        heads[url] = parsed
        return parsed
    }

    /// The cheap half of a transcript: all the run→session matcher needs.
    struct SessionHead: Equatable, Sendable {
        let customTitle: String?
        let firstTimestamp: Date?
    }

    /// How far into a transcript the matcher will look for `customTitle` and
    /// the first `timestamp`. On a real archive 222 of 224 transcripts carry
    /// the title within the first five lines; the two stragglers sit at
    /// ~195 KB. A transcript that hides its title past this cap simply falls
    /// through to the timestamp rule.
    static let headByteCap = 256 * 1024

    /// What the overwhelming majority of transcripts actually need: they
    /// declare `customTitle` and `timestamp` in their opening lines. Reading
    /// the full cap for all of them regardless still moved 52 MB per lookup
    /// on a real archive, so try a small chunk first and only widen to the
    /// cap for the stragglers.
    static let headFirstChunk = 16 * 1024

    private nonisolated static func parseHead(
        url: URL, reader: any TranscriptByteReading
    ) -> SessionHead? {
        var head = scanHead(url: url, reader: reader, maxBytes: headFirstChunk)
        // Widen only when the first chunk left something unresolved *and*
        // there is more file to read.
        if head == nil || head!.customTitle == nil || head!.firstTimestamp == nil {
            let size = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
            if size > headFirstChunk {
                head = scanHead(url: url, reader: reader, maxBytes: headByteCap) ?? head
            }
        }
        return head
    }

    private nonisolated static func scanHead(
        url: URL, reader: any TranscriptByteReading, maxBytes: Int
    ) -> SessionHead? {
        guard let data = try? reader.read(url, maxBytes: maxBytes) else { return nil }
        var lines = data.split(separator: UInt8(ascii: "\n"), omittingEmptySubsequences: true)
        // A capped read can slice the last line mid-object; drop it rather
        // than hand JSONSerialization a truncated fragment.
        if data.count >= maxBytes, lines.count > 1 { lines.removeLast() }

        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let isoNoFrac = ISO8601DateFormatter()
        isoNoFrac.formatOptions = [.withInternetDateTime]

        var customTitle: String? = nil
        var firstTimestamp: Date? = nil
        for line in lines {
            guard let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any]
            else { continue }
            if let title = obj["customTitle"] as? String { customTitle = title }
            if let tsStr = obj["timestamp"] as? String, firstTimestamp == nil {
                firstTimestamp = iso.date(from: tsStr) ?? isoNoFrac.date(from: tsStr)
            }
            // Everything the matcher needs is in hand — stop reading.
            if customTitle != nil && firstTimestamp != nil { break }
        }
        return SessionHead(customTitle: customTitle, firstTimestamp: firstTimestamp)
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

    private nonisolated static func parse(
        url: URL, reader: any TranscriptByteReading
    ) throws -> ClaudeSessionActivity {
        let data = try reader.read(url, maxBytes: nil)
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

        // Split the UTF-8 bytes, not the String. `String.split(separator:)`
        // walks Characters, so it pays Unicode grapheme-breaking per byte —
        // 44 ms vs 7 ms on a 2.3 MB transcript, and it forced the whole file
        // to be materialised as a String first.
        for lineData in data.split(separator: UInt8(ascii: "\n"), omittingEmptySubsequences: true) {
            guard let obj = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any]
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
