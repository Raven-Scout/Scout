import Foundation
import Testing
@testable import Scout

/// Covers the claude-code session JSONL reader: session→run matching (by
/// `customTitle` fragment, then by nearest first-timestamp), the per-tool
/// input summarizer, and the aggregate stats the Usage rail card renders.
@Suite("ClaudeSessionService")
struct ClaudeSessionServiceTests {

    // MARK: - Fixture helpers

    private func makeDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("claude-sessions-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// `customTitle` fragment the service matches on: `…-yyyyMMdd-HHmm`, in the
    /// local time zone (the service uses a bare `DateFormatter`).
    private func titleFragment(for date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmm"
        return f.string(from: date)
    }

    private func isoString(_ date: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.string(from: date)
    }

    /// One JSONL line carrying a single `tool_use` block.
    private func toolUseLine(
        sessionId: String,
        customTitle: String?,
        timestamp: String,
        id: String,
        name: String,
        inputJSON: String
    ) -> String {
        let titlePart = customTitle.map { "\"customTitle\":\"\($0)\"," } ?? ""
        return """
        {"sessionId":"\(sessionId)",\(titlePart)"timestamp":"\(timestamp)",\
        "message":{"content":[{"type":"tool_use","id":"\(id)","name":"\(name)",\
        "input":\(inputJSON)}]}}
        """
    }

    @discardableResult
    private func writeSession(
        in dir: URL, file: String, lines: [String]
    ) throws -> URL {
        let url = dir.appendingPathComponent(file)
        try lines.joined(separator: "\n").write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    // MARK: - defaultScoutSessionsDirectory

    @Test("Scout directory path is slug-encoded under ~/.claude/projects")
    func defaultSessionsDirectory_encodesPathWithDashes() {
        let scout = URL(fileURLWithPath: "/Users/alex/Scout")
        let dir = ClaudeSessionService.defaultScoutSessionsDirectory(scoutDirectory: scout)
        #expect(dir.lastPathComponent == "-Users-alex-Scout")
        #expect(dir.deletingLastPathComponent().lastPathComponent == "projects")
        #expect(dir.path.hasPrefix(FileManager.default.homeDirectoryForCurrentUser.path))
    }

    // MARK: - ClaudeSessionActivity derived values

    private func call(_ name: String, path: String? = nil, id: String = UUID().uuidString)
        -> ClaudeSessionActivity.ToolCall {
        ClaudeSessionActivity.ToolCall(
            id: id, name: name, timestamp: nil, summary: "", filePath: path, isError: false)
    }

    @Test("byTool buckets calls by name, most frequent first")
    func byTool_sortsDescendingByCount() {
        let activity = ClaudeSessionActivity(
            sessionId: "s", customTitle: nil, firstTimestamp: nil,
            calls: [call("Bash"), call("Read"), call("Bash"), call("Bash"), call("Read")])
        let counts = activity.byTool
        #expect(counts.first?.name == "Bash")
        #expect(counts.first?.count == 3)
        #expect(counts.count == 2)
        #expect(counts.last?.name == "Read")
        #expect(counts.last?.count == 2)
    }

    @Test("file lists de-duplicate, sort, and split by tool")
    func fileLists_dedupeAndPartitionByTool() {
        let activity = ClaudeSessionActivity(
            sessionId: "s", customTitle: nil, firstTimestamp: nil,
            calls: [
                call("Read", path: "/b.md"),
                call("Read", path: "/a.md"),
                call("Read", path: "/a.md"),      // duplicate collapses
                call("Read"),                      // nil path is dropped
                call("Edit", path: "/e.md"),
                call("NotebookEdit", path: "/n.ipynb"),
                call("Write", path: "/w.md"),
                call("Bash"),
            ])
        #expect(activity.filesRead == ["/a.md", "/b.md"])
        #expect(activity.filesEdited == ["/e.md", "/n.ipynb"])
        #expect(activity.filesWritten == ["/w.md"])
    }

    // MARK: - AggregateStats derived values

    @Test("AggregateStats exposes per-tool shortcuts and mutation total")
    func aggregateStats_derivedCounters() {
        let stats = ClaudeSessionService.AggregateStats(
            totalToolCalls: 12,
            byTool: ["Bash": 5, "WebFetch": 2, "WebSearch": 1, "Read": 4],
            uniqueFilesEdited: 3, uniqueFilesWritten: 2, uniqueFilesRead: 7)
        #expect(stats.bashCalls == 5)
        #expect(stats.webFetches == 2)
        #expect(stats.webSearches == 1)
        #expect(stats.fileMutations == 5)          // 3 edited + 2 written
        #expect(stats.topTools.map(\.name) == ["Bash", "Read", "WebFetch"])
        #expect(stats.topTools.map(\.count) == [5, 4, 2])
    }

    @Test("AggregateStats shortcuts default to zero for absent tools")
    func aggregateStats_absentToolsAreZero() {
        let stats = ClaudeSessionService.AggregateStats(
            totalToolCalls: 0, byTool: [:],
            uniqueFilesEdited: 0, uniqueFilesWritten: 0, uniqueFilesRead: 0)
        #expect(stats.bashCalls == 0)
        #expect(stats.webFetches == 0)
        #expect(stats.webSearches == 0)
        #expect(stats.fileMutations == 0)
        #expect(stats.topTools.isEmpty)
    }

    // MARK: - activity(for:) — title matching

    @Test("matches the session whose customTitle ends with the run's yyyyMMdd-HHmm")
    func activity_matchesOnCustomTitleFragment() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let fragment = titleFragment(for: started)

        try writeSession(in: dir, file: "other.jsonl", lines: [
            toolUseLine(sessionId: "other", customTitle: "scout-dreaming-20200101-0000",
                        timestamp: isoString(started), id: "x", name: "Bash",
                        inputJSON: #"{"command":"echo no"}"#),
        ])
        try writeSession(in: dir, file: "wanted.jsonl", lines: [
            toolUseLine(sessionId: "wanted-session", customTitle: "scout-briefing-\(fragment)",
                        timestamp: isoString(started), id: "t1", name: "Bash",
                        inputJSON: #"{"command":"ls -la"}"#),
        ])

        let svc = ClaudeSessionService(projectsDirectory: dir)
        let activity = await svc.activity(for: Run.make(startedAt: started))

        #expect(activity?.sessionId == "wanted-session")
        #expect(activity?.calls.count == 1)
        #expect(activity?.calls.first?.summary == "ls -la")
        #expect(activity?.calls.first?.name == "Bash")
        #expect(activity?.calls.first?.isError == false)
    }

    @Test("a second lookup is served from the in-actor cache")
    func activity_cachesParsedSessions() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let fragment = titleFragment(for: started)
        let url = try writeSession(in: dir, file: "s.jsonl", lines: [
            toolUseLine(sessionId: "cached", customTitle: "scout-briefing-\(fragment)",
                        timestamp: isoString(started), id: "t1", name: "Read",
                        inputJSON: #"{"file_path":"/notes.md"}"#),
        ])

        let svc = ClaudeSessionService(projectsDirectory: dir)
        let run = Run.make(startedAt: started)
        let first = await svc.activity(for: run)
        #expect(first?.sessionId == "cached")

        // Deleting the file must not change the answer — it is cached in-actor.
        try FileManager.default.removeItem(at: url)
        try writeSession(in: dir, file: "s.jsonl", lines: [
            toolUseLine(sessionId: "REWRITTEN", customTitle: "scout-briefing-\(fragment)",
                        timestamp: isoString(started), id: "t1", name: "Read",
                        inputJSON: #"{"file_path":"/notes.md"}"#),
        ])
        let second = await svc.activity(for: run)
        #expect(second?.sessionId == "cached")
    }

    // MARK: - activity(for:) — timestamp fallback

    @Test("falls back to the session starting closest to the run, within 10 minutes")
    func activity_fallsBackToNearestTimestamp() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)

        // Neither title matches, so the ±600s proximity rule decides.
        try writeSession(in: dir, file: "far.jsonl", lines: [
            toolUseLine(sessionId: "far", customTitle: "unrelated-title",
                        timestamp: isoString(started.addingTimeInterval(500)),
                        id: "a", name: "Bash", inputJSON: #"{"command":"far"}"#),
        ])
        try writeSession(in: dir, file: "near.jsonl", lines: [
            toolUseLine(sessionId: "near", customTitle: "unrelated-title",
                        timestamp: isoString(started.addingTimeInterval(30)),
                        id: "b", name: "Bash", inputJSON: #"{"command":"near"}"#),
        ])

        let svc = ClaudeSessionService(projectsDirectory: dir)
        let activity = await svc.activity(for: Run.make(startedAt: started))
        #expect(activity?.sessionId == "near")
    }

    @Test("a session more than 10 minutes away is not matched")
    func activity_ignoresSessionsOutsideTheWindow() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        try writeSession(in: dir, file: "stale.jsonl", lines: [
            toolUseLine(sessionId: "stale", customTitle: "unrelated",
                        timestamp: isoString(started.addingTimeInterval(3600)),
                        id: "a", name: "Bash", inputJSON: #"{"command":"x"}"#),
        ])
        let svc = ClaudeSessionService(projectsDirectory: dir)
        #expect(await svc.activity(for: Run.make(startedAt: started)) == nil)
    }

    @Test("a missing projects directory yields no activity")
    func activity_missingDirectoryReturnsNil() async {
        let absent = FileManager.default.temporaryDirectory
            .appendingPathComponent("absent-\(UUID().uuidString)")
        let svc = ClaudeSessionService(projectsDirectory: absent)
        #expect(await svc.activity(for: Run.make()) == nil)
    }

    @Test("non-jsonl files in the directory are ignored")
    func activity_ignoresNonJSONLFiles() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let fragment = titleFragment(for: started)
        try writeSession(in: dir, file: "notes.txt", lines: [
            toolUseLine(sessionId: "txt", customTitle: "scout-briefing-\(fragment)",
                        timestamp: isoString(started), id: "a", name: "Bash",
                        inputJSON: #"{"command":"x"}"#),
        ])
        let svc = ClaudeSessionService(projectsDirectory: dir)
        #expect(await svc.activity(for: Run.make(startedAt: started)) == nil)
    }

    @Test("malformed and non-tool_use lines are skipped without failing the parse")
    func activity_toleratesMalformedLines() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let fragment = titleFragment(for: started)
        try writeSession(in: dir, file: "s.jsonl", lines: [
            "not json at all",
            "{\"broken\": ",
            #"{"customTitle":"scout-briefing-\#(fragment)"}"#,
            // a text block, not a tool_use — must not become a call
            #"{"message":{"content":[{"type":"text","text":"hello"}]}}"#,
            // tool_use missing `id` — skipped
            #"{"message":{"content":[{"type":"tool_use","name":"Bash"}]}}"#,
            toolUseLine(sessionId: "s", customTitle: nil, timestamp: isoString(started),
                        id: "ok", name: "Glob", inputJSON: #"{"pattern":"**/*.swift"}"#),
        ])
        let svc = ClaudeSessionService(projectsDirectory: dir)
        let activity = await svc.activity(for: Run.make(startedAt: started))
        #expect(activity?.calls.count == 1)
        #expect(activity?.calls.first?.summary == "**/*.swift")
    }

    @Test("sessionId falls back to the file name when no line carries one")
    func activity_sessionIdDefaultsToFileName() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let fragment = titleFragment(for: started)
        try writeSession(in: dir, file: "abc-123.jsonl", lines: [
            #"{"customTitle":"scout-briefing-\#(fragment)","timestamp":"\#(isoString(started))"}"#,
        ])
        let svc = ClaudeSessionService(projectsDirectory: dir)
        let activity = await svc.activity(for: Run.make(startedAt: started))
        #expect(activity?.sessionId == "abc-123")
        #expect(activity?.calls.isEmpty == true)
    }

    @Test("timestamps without fractional seconds still parse")
    func activity_parsesNonFractionalTimestamps() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]

        try writeSession(in: dir, file: "s.jsonl", lines: [
            toolUseLine(sessionId: "plain", customTitle: "unrelated",
                        timestamp: plain.string(from: started.addingTimeInterval(5)),
                        id: "a", name: "Bash", inputJSON: #"{"command":"x"}"#),
        ])
        let svc = ClaudeSessionService(projectsDirectory: dir)
        let activity = await svc.activity(for: Run.make(startedAt: started))
        #expect(activity?.sessionId == "plain")
        #expect(activity?.firstTimestamp != nil)
        #expect(activity?.calls.first?.timestamp != nil)
    }

    // MARK: - summarize() — one case per tool

    @Test("each tool renders a one-line input summary")
    func summarize_perToolRendering() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let fragment = titleFragment(for: started)
        let ts = isoString(started)

        let cases: [(name: String, input: String, expected: String)] = [
            ("Bash",      #"{"command":"swift build"}"#,                    "swift build"),
            ("Read",      #"{"file_path":"/a.md"}"#,                        "/a.md"),
            ("Edit",      #"{"file_path":"/b.md"}"#,                        "/b.md"),
            ("Write",     #"{"file_path":"/c.md"}"#,                        "/c.md"),
            ("Glob",      #"{"pattern":"*.swift"}"#,                        "*.swift"),
            ("Grep",      #"{"pattern":"TODO","path":"/src"}"#,             "TODO in /src"),
            ("Grep",      #"{"pattern":"TODO"}"#,                           "TODO"),
            ("WebFetch",  #"{"url":"https://example.com"}"#,                "https://example.com"),
            ("WebSearch", #"{"query":"swift testing"}"#,                    "swift testing"),
            ("TodoWrite", #"{"todos":[{"c":"a"},{"c":"b"}]}"#,              "2 todos"),
            ("TodoWrite", #"{"todos":[{"c":"a"}]}"#,                        "1 todo"),
            ("TodoWrite", #"{}"#,                                           ""),
            ("ToolSearch", #"{"query":"select:Read"}"#,                     "select:Read"),
            // unknown tool: best-effort first short string field
            ("MysteryTool", #"{"only":"fallback-value"}"#,                  "fallback-value"),
            ("MysteryTool", #"{}"#,                                         ""),
            // missing expected keys fall back to "?"
            ("Read",      #"{}"#,                                           "?"),
            ("Glob",      #"{}"#,                                           "?"),
            ("WebFetch",  #"{}"#,                                           "?"),
            ("WebSearch", #"{}"#,                                           "?"),
            ("ToolSearch", #"{}"#,                                          "?"),
        ]

        var lines = [#"{"customTitle":"scout-briefing-\#(fragment)","timestamp":"\#(ts)"}"#]
        for (i, c) in cases.enumerated() {
            lines.append(toolUseLine(sessionId: "s", customTitle: nil, timestamp: ts,
                                     id: "t\(i)", name: c.name, inputJSON: c.input))
        }
        try writeSession(in: dir, file: "s.jsonl", lines: lines)

        let svc = ClaudeSessionService(projectsDirectory: dir)
        let activity = try #require(await svc.activity(for: Run.make(startedAt: started)))
        #expect(activity.calls.count == cases.count)
        for (i, c) in cases.enumerated() {
            #expect(activity.calls[i].name == c.name)
            #expect(activity.calls[i].summary == c.expected,
                    "\(c.name) with \(c.input) summarized as \(activity.calls[i].summary)")
        }
    }

    @Test("file path is lifted from file_path, path, or notebook_path")
    func parse_filePathAlternateKeys() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let fragment = titleFragment(for: started)
        let ts = isoString(started)
        try writeSession(in: dir, file: "s.jsonl", lines: [
            #"{"customTitle":"scout-briefing-\#(fragment)","timestamp":"\#(ts)"}"#,
            toolUseLine(sessionId: "s", customTitle: nil, timestamp: ts, id: "a",
                        name: "Read", inputJSON: #"{"file_path":"/one.md"}"#),
            toolUseLine(sessionId: "s", customTitle: nil, timestamp: ts, id: "b",
                        name: "Grep", inputJSON: #"{"pattern":"x","path":"/two"}"#),
            toolUseLine(sessionId: "s", customTitle: nil, timestamp: ts, id: "c",
                        name: "NotebookEdit", inputJSON: #"{"notebook_path":"/three.ipynb"}"#),
        ])
        let svc = ClaudeSessionService(projectsDirectory: dir)
        let activity = try #require(await svc.activity(for: Run.make(startedAt: started)))
        #expect(activity.calls.map(\.filePath) == ["/one.md", "/two", "/three.ipynb"])
    }

    // MARK: - aggregateStats(for:)

    @Test("aggregate stats sum calls and union file sets across runs")
    func aggregateStats_acrossRuns() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let startA = Date(timeIntervalSince1970: 1_781_600_000)
        let startB = startA.addingTimeInterval(7200)   // well outside the ±600s window

        try writeSession(in: dir, file: "a.jsonl", lines: [
            toolUseLine(sessionId: "a", customTitle: "scout-briefing-\(titleFragment(for: startA))",
                        timestamp: isoString(startA), id: "a1", name: "Bash",
                        inputJSON: #"{"command":"x"}"#),
            toolUseLine(sessionId: "a", customTitle: nil, timestamp: isoString(startA),
                        id: "a2", name: "Edit", inputJSON: #"{"file_path":"/shared.md"}"#),
            toolUseLine(sessionId: "a", customTitle: nil, timestamp: isoString(startA),
                        id: "a3", name: "Read", inputJSON: #"{"file_path":"/r1.md"}"#),
        ])
        try writeSession(in: dir, file: "b.jsonl", lines: [
            toolUseLine(sessionId: "b", customTitle: "scout-dreaming-\(titleFragment(for: startB))",
                        timestamp: isoString(startB), id: "b1", name: "Bash",
                        inputJSON: #"{"command":"y"}"#),
            // same path as run A — the unique-file union must not double-count
            toolUseLine(sessionId: "b", customTitle: nil, timestamp: isoString(startB),
                        id: "b2", name: "Edit", inputJSON: #"{"file_path":"/shared.md"}"#),
            toolUseLine(sessionId: "b", customTitle: nil, timestamp: isoString(startB),
                        id: "b3", name: "Write", inputJSON: #"{"file_path":"/w1.md"}"#),
        ])

        let svc = ClaudeSessionService(projectsDirectory: dir)
        let stats = await svc.aggregateStats(for: [
            Run.make(type: .morningBriefing, startedAt: startA),
            Run.make(type: .dreaming, startedAt: startB),
        ])

        #expect(stats.totalToolCalls == 6)
        #expect(stats.bashCalls == 2)
        #expect(stats.byTool["Edit"] == 2)
        #expect(stats.uniqueFilesEdited == 1)     // /shared.md counted once
        #expect(stats.uniqueFilesWritten == 1)
        #expect(stats.uniqueFilesRead == 1)
        #expect(stats.fileMutations == 2)
    }

    @Test("runs with no matching session contribute nothing")
    func aggregateStats_skipsUnmatchedRuns() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let stats = await ClaudeSessionService(projectsDirectory: dir)
            .aggregateStats(for: [Run.make(), Run.make()])
        #expect(stats.totalToolCalls == 0)
        #expect(stats.byTool.isEmpty)
        #expect(stats.uniqueFilesEdited == 0)
        #expect(stats.uniqueFilesWritten == 0)
        #expect(stats.uniqueFilesRead == 0)
    }

    @Test("aggregate over an empty run list is all zeroes")
    func aggregateStats_emptyRunList() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let stats = await ClaudeSessionService(projectsDirectory: dir).aggregateStats(for: [])
        #expect(stats == ClaudeSessionService.AggregateStats(
            totalToolCalls: 0, byTool: [:],
            uniqueFilesEdited: 0, uniqueFilesWritten: 0, uniqueFilesRead: 0))
    }
}
