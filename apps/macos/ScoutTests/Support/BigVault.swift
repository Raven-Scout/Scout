import Combine
import Foundation
@testable import Scout

/// A synthetic vault at the scale of a real, long-used one (UI audit card C6):
/// hundreds of action items with paragraph-length titles, hundreds of KB notes
/// with long names, ~150 sessions across many projects, dozens of wishlist,
/// research and proposal files, two weeks of run logs and many connectors with
/// long names. Layout problems only show at this size: clipped filter rows,
/// wrapped titles, cards a screen tall.
///
/// Everything is generated and anonymised (CLAUDE.md): the shared stand-ins
/// Alex / Priya / Sam, `PROJ-` / `OPS-` ids, `example-org`, generic nouns.
/// Nothing reads the real home — the sessions come from a generated
/// `sessions-index.json` behind a `FileSessionIndexSource`, and every
/// `scoutctl` call is answered by a `RuleBasedRunner`.
///
/// To look at it in a Debug build (under a separate bundle ID only), write it
/// to a folder with `BigVault(scale: .full, at: folder)` (kept on disk by
/// `tearDown()`), then point that build's `scoutDataDir` default at the folder
/// and its `sessionsIndexFile` default at `<folder>/sessions-index.big.json`.
@MainActor
struct BigVault {
    struct Scale: Sendable {
        var actionItems = 320
        var kbNotes = 400
        var sessions = 150
        var projects = 14
        var wishlist = 60
        var research = 40
        var proposals = 24
        var runDays = 12
        var connectors = 36
        var slots = 14

        static let full = Scale()
        /// Enough to overflow every row and list, small enough for a test
        /// that renders many sizes.
        static let layout = Scale(actionItems: 160, kbNotes: 160, sessions: 150, wishlist: 30,
                                  research: 20, proposals: 12, runDays: 8, connectors: 36, slots: 14)
    }

    /// The action-items day the fixture writes.
    static let day = "2026-06-15"
    static var dayDate: Date {
        var comps = DateComponents()
        comps.year = 2026; comps.month = 6; comps.day = 15; comps.hour = 12
        return Calendar(identifier: .gregorian).date(from: comps)!
    }

    let root: URL
    let state: AppState
    let updates: UpdateService
    let runner: RuleBasedRunner
    /// A generated `sessions-index.json`; the Sessions page reads it through
    /// a `FileSessionIndexSource`, never `scoutctl session index`.
    let sessionsIndexFile: URL
    /// When true, `tearDown()` leaves the folder (a written-to-disk vault).
    private let keepOnDisk: Bool

    /// Write a vault under a fresh temp folder, or under `directory` (which is
    /// then kept by `tearDown()`).
    init(scale: Scale = .layout, at directory: URL? = nil, now: Date = Date()) throws {
        let root = (directory ?? FileManager.default.temporaryDirectory
            .appendingPathComponent("big-vault-\(UUID().uuidString)"))
            .resolvingSymlinksInPath()
        self.root = root
        self.keepOnDisk = directory != nil
        try Self.writeVault(at: root, scale: scale, now: now)

        let runner = RuleBasedRunner()
        runner.on(tool: "false", prefix: ["schedule", "list-upcoming"], stdout: Self.upcomingJSON(scale: scale, now: now))
        runner.on(tool: "false", prefix: ["schedule", "list"], stdout: Self.slotListJSON(scale: scale))
        self.runner = runner

        self.sessionsIndexFile = root.appendingPathComponent("sessions-index.big.json")
        var config = AppState.Configuration.testing(scoutDirectory: root, runner: runner)
        config.agentSessionSource = FileSessionIndexSource(file: sessionsIndexFile)
        self.state = AppState(configuration: config)
        self.updates = SmokeVault.inertUpdates()
    }

    /// Load every service the pages read, the way `startsBackgroundWork`
    /// would at launch, but synchronously and without any watch.
    func load() async {
        _ = try? await state.trackerService.loadInitial()
        _ = try? await state.connectorHealthService.loadInitial()
        _ = try? await state.sessionLogService.loadInitial()
        await state.scheduleService.refresh()
        try? await state.scheduleEditService.loadAll()
        state.proposalsDocumentService.load()
        state.wishlistDocumentService.load()
        state.researchDocumentService.load()
        state.knowledgeBaseService.load()
        await state.knowledgeBaseService.reparseAndWait()
        await state.actionItemsDocumentService.load(date: Self.dayDate)
        await state.sessionIndexService.refreshFast()
    }

    func tearDown() {
        guard !keepOnDisk else { return }
        try? FileManager.default.removeItem(at: root)
    }

    // MARK: - Writing

    private static func writeVault(at root: URL, scale: Scale, now: Date) throws {
        let fm = FileManager.default
        for sub in ["action-items", VaultLayout.dreamingProposalsDir, PerFileTabConfig.wishlist.directoryDefaultRelative,
                    PerFileTabConfig.research.directoryDefaultRelative, ".scout-logs", ".scout-state", ".scout-cache"] {
            try fm.createDirectory(at: root.appendingPathComponent(sub), withIntermediateDirectories: true)
        }
        try write(actionItems(scale: scale), to: root.appendingPathComponent("action-items/action-items-\(day).md"))
        try writeKB(at: root.appendingPathComponent("knowledge-base"), scale: scale)
        for i in 0..<scale.wishlist {
            try write(perFileItem(i, kind: "wishlist"), to: root.appendingPathComponent(
                "\(PerFileTabConfig.wishlist.directoryDefaultRelative)/2026-06-\(String(format: "%02d", 1 + i % 28))-\(slug(longTitle(i), max: 70))-\(i).md"))
        }
        for i in 0..<scale.research {
            try write(perFileItem(i, kind: "research"), to: root.appendingPathComponent(
                "\(PerFileTabConfig.research.directoryDefaultRelative)/2026-06-\(String(format: "%02d", 1 + i % 28))-\(slug(longTitle(i + 7), max: 70))-\(i).md"))
        }
        for i in 0..<scale.proposals {
            try write(proposal(i), to: root.appendingPathComponent(
                "\(VaultLayout.dreamingProposalsDir)/2026-06-\(String(format: "%02d", 1 + i % 28))-\(slug(longTitle(i + 3), max: 60))-\(i).md"))
        }
        try writeLogs(at: root.appendingPathComponent(".scout-logs"), scale: scale, now: now)
        try write(sessionsIndex(scale: scale, now: now), to: root.appendingPathComponent("sessions-index.big.json"))
    }

    private static func write(_ text: String, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try text.write(to: url, atomically: true, encoding: .utf8)
    }

    // MARK: - Words

    private static let subjects = [
        "the tracing job", "the demo", "the quarterly planning doc", "the cost-attribution report",
        "the onboarding checklist", "the release train", "the partner integration", "the search index",
        "the nightly export", "the billing migration", "the design review queue", "the support rotation",
    ]
    private static let verbs = [
        "Follow up with Priya on", "Review Sam's proposal for", "Draft the rollout plan for",
        "Decide whether to pause", "Reply to Alex about", "Unblock the migration of",
        "Write the retrospective for", "Check the numbers behind",
    ]
    private static let tails = [
        "because the partner team asked for a firm date before Thursday's steering meeting and nobody has owned the answer since the last reorg",
        "now that the second vendor quote came in 40% higher than the first and finance wants a recommendation with a fallback option by end of week",
        "after the incident review flagged three gaps in alerting that nobody picked up, including the paging policy for weekends and the runbook links",
        "so the next run can stop re-deriving the same context from scratch every morning and the briefing gets shorter",
    ]

    /// A paragraph-length title, as a long-used vault's briefings write them.
    static func longTitle(_ i: Int) -> String {
        "\(verbs[i % verbs.count]) \(subjects[i % subjects.count]) \(tails[i % tails.count])"
    }

    static func slug(_ s: String, max: Int) -> String {
        let lowered = s.lowercased().map { $0.isLetter || $0.isNumber ? $0 : "-" }
        let collapsed = String(lowered).split(separator: "-").joined(separator: "-")
        return String(collapsed.prefix(max))
    }

    // MARK: - Action items

    private static func actionItems(scale: Scale) -> String {
        let sections: [(String, Double)] = [
            ("## 🔴 Urgent / Time-Sensitive", 0.2), ("## 🟡 To Do", 0.4),
            ("## 🟢 Watching", 0.25), ("## Personal (from knowledge graph)", 0.15),
        ]
        var out = "# Action Items — Monday, June 15, 2026\n"
        out += "**Morning briefing** — Generated 7:08 AM. **Consolidated 11:00 AM** (standup outcomes, two partner replies, the billing migration slipped a day, and \(longTitle(1).lowercased())).\n\n"
        out += "**This run's headline** — \(longTitle(2)). Also: \(longTitle(3).lowercased()).\n\n---\n\n"
        out += "## 💡 Focus\n\n"
        for i in 0..<4 {
            out += "- [ ] **\(longTitle(i + 11))** — \(tails[(i + 1) % tails.count]). [[PROJ-\(2100 + i)]] [[\(slug(subjects[i], max: 40))]]\n"
        }
        out += "\n---\n\n"
        var n = 0
        for (heading, share) in sections {
            out += "\(heading)\n\n"
            let count = Int(Double(scale.actionItems) * share)
            for _ in 0..<count {
                let tag = n % 3 == 0 ? "[#OPS\(n)] " : ""
                let done = n % 9 == 4 ? "x" : " "
                out += "- [\(done)] \(tag)**\(longTitle(n))** — \(tails[(n + 2) % tails.count]). [[PROJ-\(3000 + n)]] [[\(slug(subjects[n % subjects.count], max: 40))]]\n"
                if n % 7 == 0 {
                    out += "  > alex (2026-06-15 9:15 AM): \(tails[n % tails.count]).\n"
                }
                n += 1
            }
            out += "\n---\n\n"
        }
        out += "## 📅 Meetings Today\n\n| Time | Meeting | Prep |\n| --- | --- | --- |\n"
        for i in 0..<8 {
            out += "| \(9 + i):00 | \(longTitle(i).prefix(70)) | \(tails[i % tails.count].prefix(90)) |\n"
        }
        out += "\n---\n\n## 📋 Session Digest\n\n"
        for i in 0..<12 {
            out += "- \(longTitle(i + 5)) — see [PR](https://github.com/example-org/app/pull/\(100 + i)).\n"
        }
        return out
    }

    // MARK: - Knowledge base

    private static func writeKB(at kb: URL, scale: Scale) throws {
        let folders = ["people", "projects", "topics", "meetings/2026", "areas/operations/runbooks"]
        for i in 0..<scale.kbNotes {
            let folder = folders[i % folders.count]
            let name = "\(slug(longTitle(i), max: 90))-\(i)"
            let type = folder.hasPrefix("people") ? "person" : folder.hasPrefix("projects") ? "project" : "topic"
            let links = (1...4).map { "[[\(slug(longTitle(i + $0), max: 90))-\(i + $0)]]" }.joined(separator: ", ")
            try write("""
                ---
                type: \(type)
                ---
                # \(longTitle(i))

                \(tails[i % tails.count].prefix(1).uppercased() + tails[i % tails.count].dropFirst()).

                Related: \(links).
                """, to: kb.appendingPathComponent("\(folder)/\(name).md"))
        }
    }

    // MARK: - Wishlist, research, proposals

    private static func perFileItem(_ i: Int, kind: String) -> String {
        let statuses = ["open", "in-progress", "blocked", "done"]
        let priorities = ["high", "medium", "low"]
        var progress = ""
        for d in 0..<(i % 4 == 0 ? 18 : 3) {
            progress += "- 2026-06-\(String(format: "%02d", 1 + d)): \(tails[(i + d) % tails.count]).\n"
        }
        return """
            ---
            title: \(longTitle(i + (kind == "research" ? 7 : 0)))
            status: \(statuses[i % statuses.count])
            priority: \(priorities[i % priorities.count])
            date: 2026-06-\(String(format: "%02d", 1 + i % 28))
            area: \(subjects[i % subjects.count])
            ---

            # \(longTitle(i))
            \(tails[i % tails.count]).

            ## Progress
            \(progress)
            """
    }

    private static func proposal(_ i: Int) -> String {
        let status = i % 3 == 0 ? "Applied" : "Pending (auto-apply after 2026-06-\(String(format: "%02d", 20 + i % 8)))"
        return """
            ---
            date: 2026-06-\(String(format: "%02d", 1 + i % 28))
            title: \(longTitle(i + 3))
            status: \(status)
            target: SKILL.md
            ---

            # 2026-06-\(String(format: "%02d", 1 + i % 28)) — \(longTitle(i + 3))
            **Trigger:** \(tails[i % tails.count]).

            ```
            cooldown_minutes: \(30 + i)
            a_very_long_configuration_key_that_does_not_wrap_on_its_own_and_runs_past_the_card_edge: true
            ```
            """
    }

    // MARK: - Logs, usage, connectors

    /// Connector keys with long names, the way MCP servers and plugin
    /// connectors show up in `connector-calls-*.jsonl`.
    static func connectorKey(_ i: Int) -> String {
        let names = ["plugin_tracing_job_observability_and_cost_attribution_bridge",
                     "claude_ai_shared_team_calendar_for_the_partner_integration",
                     "plugin_search_index_reindexing_and_freshness_monitor",
                     "local_billing_migration_ledger_reader"]
        return "mcp:\(names[i % names.count])_\(i)"
    }

    private static func writeLogs(at logs: URL, scale: Scale, now: Date) throws {
        let cal = Calendar.current
        let stamp = DateFormatter()
        stamp.locale = Locale(identifier: "en_US_POSIX")
        stamp.dateFormat = "yyyy-MM-dd_HH-mm"
        let human = DateFormatter()
        human.locale = Locale(identifier: "en_US_POSIX")
        human.dateFormat = "EEE MMM d HH:mm:ss zzz yyyy"
        let iso = ISO8601DateFormatter()
        var usage = ""
        for dayOffset in 0..<scale.runDays {
            guard let day = cal.date(byAdding: .day, value: -dayOffset, to: cal.startOfDay(for: now)) else { continue }
            var calls = ""
            for (hour, runner) in [(8, "scout"), (11, "scout"), (13, "scout"), (17, "scout"), (19, "dreaming"), (21, "research")] {
                guard let start = cal.date(bySettingHour: hour, minute: 3, second: 0, of: day), start < now else { continue }
                let end = start.addingTimeInterval(TimeInterval(300 + hour * 20))
                let failed = (dayOffset + hour) % 5 == 0
                try write("""
                    === SCOUT run starting at \(human.string(from: start)) ===
                    Reading action items…
                    \(failed ? "Error: API Error: 429 rate limited while calling \(connectorKey(hour))" : "Wrote the briefing.")
                    === SCOUT run finished at \(human.string(from: end)) (exit \(failed ? 1 : 0)) ===
                    """, to: logs.appendingPathComponent("\(runner)-\(stamp.string(from: start)).log"))
                usage += #"{"ts":"\#(iso.string(from: end))","type":"consolidation","budget_cap":10,"budget_spent":0,"exit_code":\#(failed ? 1 : 0),"source":"runner"}"# + "\n"
                for c in 0..<scale.connectors {
                    let error = (c + hour + dayOffset) % 6 == 0
                    calls += #"{"ts":"\#(iso.string(from: start))","session_id":"s\#(dayOffset)-\#(hour)","mode":"briefing","tool":"mcp__x__call","connector":"\#(connectorKey(c))","error":\#(error)}"# + "\n"
                }
            }
            let dayStamp = String(stamp.string(from: day).prefix(10))
            try write(calls, to: logs.appendingPathComponent("connector-calls-\(dayStamp).jsonl"))
        }
        try write(usage, to: logs.appendingPathComponent("usage-tracker.jsonl"))
    }

    // MARK: - Schedules (answered by the runner)

    static func slotKey(_ i: Int) -> String {
        ["morning-briefing", "weekend-briefing-for-the-partner-integration-team",
         "afternoon-consolidation-after-the-standup-and-partner-sync",
         "evening-dreaming", "research"][i % 5] + (i < 5 ? "" : "-\(i)")
    }

    private static func slotListJSON(scale: Scale) -> String {
        let types = ["briefing", "briefing", "consolidation", "dreaming", "research"]
        let rows = (0..<scale.slots).map { i in
            #"{"key":"\#(slotKey(i))","type":"\#(types[i % 5])","runner":"run-scout.sh","fires_at_local":"\#(String(format: "%02d:00", 6 + i % 18))","weekdays":["Mon","Tue","Wed","Thu","Fri"],"missed_window_hours":4,"on_miss":"fire","cooldown_minutes":60,"budget_usd":null,"tz":null,"runtime":"local"}"#
        }
        return "[" + rows.joined(separator: ",") + "]"
    }

    private static func upcomingJSON(scale: Scale, now: Date) -> String {
        let types = ["briefing", "briefing", "consolidation", "dreaming", "research"]
        let iso = ISO8601DateFormatter()
        let rows = (0..<scale.slots).map { i in
            let at = iso.string(from: now.addingTimeInterval(TimeInterval(1800 * (i + 1))))
            return #"{"slot_key":"\#(slotKey(i))","slot_type":"\#(types[i % 5])","scheduled_at_local":"\#(at)","scheduled_at_utc":"\#(at)"}"#
        }
        return "[" + rows.joined(separator: ",") + "]"
    }

    // MARK: - Sessions

    /// `scale.sessions` sessions cloned from the anonymised fixture's shapes,
    /// with long titles, long project names and recent activity.
    static func sessionsIndex(scale: Scale, now: Date) throws -> String {
        var object = try SessionsFixture.object()
        let templates = object["sessions"] as? [[String: Any]] ?? []
        let iso = ISO8601DateFormatter()
        let projects = (0..<scale.projects).map { p -> (key: String, name: String) in
            let name = p % 3 == 0
                ? "the-quarterly-cross-team-observability-and-cost-attribution-initiative-\(p)"
                : "example-repo-\(p)"
            return ("/Users/alex/code/\(name)", name)
        }
        var sessions: [[String: Any]] = []
        for i in 0..<scale.sessions {
            var s = templates[i % max(templates.count, 1)]
            let project = projects[i % projects.count]
            s["id"] = "local_big_\(i)"
            s["cli_session_id"] = String(format: "bbbbbbbb-0000-0000-0000-%012d", i)
            s["title"] = longTitle(i)
            s["project_key"] = project.key
            s["group_name"] = project.name
            s["cwd"] = project.key
            s["origin_cwd"] = project.key
            s["parent_session_id"] = NSNull()
            let active = now.addingTimeInterval(-TimeInterval(600 * (i + 1)))
            s["created_at"] = iso.string(from: active.addingTimeInterval(-7200))
            s["last_activity_at"] = iso.string(from: active)
            sessions.append(s)
        }
        object["sessions"] = sessions
        object["generated_at"] = iso.string(from: now)
        object["projects"] = projects.map { ["key": $0.key, "name": $0.name, "group_id": NSNull(),
                                             "counts": [String: Int]()] as [String: Any] }
        object["source_errors"] = []
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        return String(decoding: data, as: UTF8.self)
    }
}
