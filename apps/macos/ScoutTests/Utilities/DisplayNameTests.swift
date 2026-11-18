import Foundation
import Testing
@testable import Scout

/// `DisplayName` is the one place engine identifiers become words; these pin
/// the mapping and the rule that no raw key ever reaches the owner.
@Suite("Display names")
struct DisplayNameTests {

    /// True when `label` still looks like an engine identifier: an
    /// underscore, a dash between letters, or a camelCase hump.
    static func looksRaw(_ label: String) -> Bool {
        if label.contains("_") { return true }
        if label.range(of: #"[a-z]-[a-z]"#, options: .regularExpression) != nil { return true }
        return label.range(of: #"[a-z][A-Z]"#, options: .regularExpression) != nil
    }

    // MARK: - Fallback

    @Test("humanize turns any identifier into sentence case", arguments: [
        ("morningBriefing", "Morning briefing"),
        ("skipped_token_ceiling", "Skipped token ceiling"),
        ("slot.never-ran", "Slot never ran"),
        ("dreaming-weekend-morning", "Dreaming weekend morning"),
        ("APIError", "API error"),
        ("rate_limited", "Rate limited"),
        ("KB", "KB"),
        ("", "Unknown"),
        ("___", "Unknown"),
    ])
    func humanize(raw: String, expected: String) {
        #expect(DisplayName.humanize(raw) == expected)
    }

    @Test("normalized keys ignore case and separators")
    func normalized() {
        #expect(DisplayName.normalized("skipped_gates") == DisplayName.normalized("skippedGates"))
        #expect(DisplayName.normalized("rate-limited") == "ratelimited")
    }

    // MARK: - Runs

    @Test("every run type has a hand-written name")
    func runTypes() {
        #expect(RunType.morningBriefing.displayName == "Morning briefing")
        #expect(RunType.weekendBriefing.displayName == "Weekend briefing")
        #expect(RunType.manual.displayName == "Manual run")
        for t in RunType.allCases {
            #expect(!Self.looksRaw(t.displayName), "\(t) → \(t.displayName)")
            #expect(t.displayName != t.rawValue)
        }
    }

    @Test("run statuses read as plain words", arguments: [
        (RunStatus.rateLimited, "Paused: usage limit"),
        (.success, "Finished"),
        (.failure, "Failed"),
        (.timeout, "Stopped: took too long"),
        (.orphaned, "Didn't finish"),
        (.skippedBudget, "Skipped: over budget"),
        (.skippedConcurrency, "Skipped: another run was going"),
        (.running, "Running"),
        (.scheduled, "Scheduled"),
    ])
    func runStatus(status: RunStatus, expected: String) {
        #expect(status.displayName == expected)
    }

    @Test("every RunStatus case, including ones added later, has an entry in the table")
    func everyRunStatusIsMapped() {
        for s in RunStatus.allCases {
            #expect(DisplayName.runStatuses[DisplayName.normalized(s.rawValue)] != nil,
                    "add \(s.rawValue) to DisplayName.runStatuses")
            #expect(!Self.looksRaw(s.displayName))
        }
    }

    @Test("a skip's reason is the second half of its status name, so both read the same")
    func skipReasonMatchesStatusName() throws {
        for s in RunStatus.allCases {
            guard let reason = s.skipReason else {
                #expect(!s.displayName.hasPrefix("Skipped"), "\(s) needs a skipReason")
                continue
            }
            #expect(s.displayName == "Skipped: " + reason.prefix(1).lowercased() + reason.dropFirst(), "\(s)")
        }
        #expect(RunStatus.skippedBudget.skipReason == "Over budget")
        #expect(RunStatus.skippedGates.skipReason == "Privacy checks not confirmed")
        #expect(DisplayName.skipReason("skipped_lock") == "Another run was going")
        #expect(DisplayName.skipReason("failed") == nil)
    }

    @Test("run-ledger outcomes share the app's wording", arguments: [
        ("ok", "Finished"),
        ("failed", "Failed"),
        ("skipped_gates", "Skipped: privacy checks not confirmed"),
        ("skipped_sandbox", "Skipped: couldn't protect this run"),
        ("skipped_runtime", "Skipped: agent not allowed"),
        ("skipped_lease", "Skipped: another app was writing"),
        ("skipped_lock", "Skipped: another run was going"),
        ("skipped_redundant", "Skipped: already done"),
        ("skipped_budget", "Skipped: over budget"),
        ("skippedGates", "Skipped: privacy checks not confirmed"),
    ])
    func ledgerOutcomes(raw: String, expected: String) {
        #expect(DisplayName.runStatus(raw) == expected)
    }

    @Test("an unknown status falls back to words, not the key")
    func unknownStatus() {
        #expect(DisplayName.runStatus("skipped_moon_phase") == "Skipped moon phase")
        #expect(DisplayName.runStatusReason("skipped_moon_phase") == nil)
        #expect(RunStatus.rateLimited.reason?.contains("usage limit") == true)
        #expect(RunStatus.success.reason == nil)
    }

    @Test("every table value reads as plain words")
    func noTableValueLooksRaw() {
        for label in DisplayName.runStatuses.values {
            #expect(!Self.looksRaw(label), "\(label)")
        }
    }

    // MARK: - Failure classes

    @Test("failure classes explain what happened and what to do", arguments: [
        "oauth_expired", "auth_race", "network", "git_lock", "rate_limited", "budget", "timeout",
        "lease_held", "lease_unavailable", "gates_unsafe_endpoint", "gates_unsafe_launcher", "unknown",
    ])
    func failureClass(raw: String) {
        let e = DisplayName.failureClass(raw)
        #expect(!Self.looksRaw(e.title), "\(raw) → \(e.title)")
        #expect(!e.why.isEmpty)
        #expect(e.fix?.isEmpty == false)
    }

    @Test("an unknown failure class is humanized and points at the log")
    func unknownFailureClass() {
        let e = DisplayName.failureClass("disk_full")
        #expect(e.title == "Disk full")
        #expect(e.fix?.contains("log") == true)
        #expect(DisplayName.failureClass("oauth_expired").title == "Signed out of Claude")
    }

    // MARK: - Schedules

    @Test("slot keys become schedule names", arguments: [
        ("morning-briefing", "Morning briefing"),
        ("evening-consolidation", "Evening consolidation"),
        ("dreaming-nightly", "Nightly dreaming"),
        ("dreaming-weekend-morning", "Weekend morning dreaming"),
        ("research", "Research"),
        ("my-friday-sweep", "My friday sweep"),
    ])
    func slotKey(key: String, expected: String) {
        #expect(DisplayName.slotKey(key) == expected)
    }

    @Test("a user key that reads like a default schedule keeps its key")
    func slotKeyCollision() {
        #expect(DisplayName.slotKey("nightly-dreaming") == "Nightly dreaming (nightly-dreaming)")
        #expect(DisplayName.slotKey("dreaming-nightly") == "Nightly dreaming")
    }

    @Test("slot types and catch-up policies have names and explanations")
    func slotTypesAndPolicies() {
        for t in SlotType.allCases {
            #expect(!Self.looksRaw(t.displayName))
            #expect(t.displayName.first?.isUppercase == true)
        }
        #expect(OnMissPolicy.fire.displayName == "Run late")
        #expect(OnMissPolicy.skip.displayName == "Skip")
        #expect(OnMissPolicy.collapse.displayName == "Latest only")
        // Engine semantics (#193): skip still runs late inside the window,
        // like fire; collapse runs only the latest missed schedule of a kind.
        #expect(OnMissPolicy.skip.explanation.contains("Runs late if the Mac wakes within the catch-up window"))
        #expect(OnMissPolicy.fire.explanation.contains("Runs late if the Mac wakes within the catch-up window"))
        #expect(OnMissPolicy.collapse.explanation.contains("only the latest one runs"))
        #expect(OnMissPolicy.collapse.explanation.contains("that also use Latest only"))
        for p in OnMissPolicy.allCases { #expect(!p.explanation.isEmpty) }
        let slot = Slot(key: "dreaming-evening", type: .dreaming, runner: "run-dreaming.sh",
                        firesAtLocal: "21:00", weekdays: ["Mon"], missedWindowHours: 2,
                        onMiss: .skip, cooldownMinutes: 0)
        #expect(slot.displayName == "Evening dreaming")
    }

    // MARK: - Connectors and health

    @Test("connector keys become product names")
    func connectors() {
        #expect(DisplayName.connector("mcp:claude_ai_Slack") == "Slack")
        #expect(DisplayName.connector("mcp:claude_ai_Google_Calendar") == "Google Calendar")
        #expect(DisplayName.connector("github") == "GitHub")
        #expect(DisplayName.connector("mcp:claude_ai_Some_Tool") == "Some Tool")
        #expect(DisplayName.connector("mcp:plugin_acme_tracker") == "Acme Tracker")
        #expect(DisplayName.connector("") == "Unknown")
    }

    @Test("each connector gets its own fix; only MCP connectors are told to use /mcp", arguments: [
        ("mcp:claude_ai_Slack", "claude.ai"),
        ("mcp:fathom", "claude.ai"),
        ("github", "GitHub command-line tool"),
        ("mcp:claude-in-chrome", "Claude in Chrome extension"),
        ("mcp:whatsapp-mcp", "WhatsApp bridge"),
        ("notify:telegram", "Telegram bot token"),
        ("mcp:acme-tracker", "/mcp"),
        ("calendar-feed", "set up and signed in"),
    ])
    func connectorFix(key: String, expected: String) throws {
        let fix = try #require(DisplayName.connectorFix(key).fix)
        #expect(fix.contains(expected), "\(key): \(fix)")
        if !key.hasPrefix("mcp:") || key.hasPrefix("mcp:claude_ai_") || key == "mcp:fathom"
            || key == "mcp:claude-in-chrome" || key == "mcp:whatsapp-mcp" {
            #expect(!fix.contains("/mcp"), "\(key) must not be told to use /mcp")
        }
    }

    @Test("privacy-check skips don't blame the Claude Code version; the engine's advice is in details")
    func gatesAdvice() {
        for raw in ["gates_unsafe_endpoint", "gates_unsafe_launcher"] {
            let e = DisplayName.failureClass(raw)
            #expect(!e.why.contains("version"))
            #expect(e.details?.contains("scoutctl gates probe") == true)
            #expect(Problem(failureClass: raw, details: "Log: x").details?.contains("Log: x") == true)
        }
        #expect(DisplayName.failureClass("gates_unsafe_launcher").details?.contains("claude_bin") == true)
        #expect(DisplayName.runStatusReason("skipped_gates")?.contains("version") == false)
    }

    @Test("alert and health levels use the glossary's health words")
    func healthWords() {
        #expect(ConnectorAlert.Level.critical.displayName == "Not working")
        #expect(ConnectorAlert.Level.warning.displayName == "Warning")
        #expect(DisplayName.health("green") == "Healthy")
        #expect(DisplayName.health("yellow") == "Needs attention")
        #expect(DisplayName.health("red") == "Not working")
        #expect(DisplayName.health("note") == "For your information")
        #expect(DisplayName.health("purple") == "Purple")
    }
}

@Suite("Problem")
struct ProblemTests {

    @Test("a run that ended well, or is still going, is not a problem")
    func noProblem() {
        #expect(Problem(run: .make(status: .success)) == nil)
        #expect(Problem(run: .make(status: .running)) == nil)
        #expect(Problem(run: .make(status: .scheduled)) == nil)
        #expect(Problem(runStatus: "ok", runName: "Research") == nil)
    }

    @Test("a failed run says what happened, why, what to do, and keeps the log in details")
    func failedRun() throws {
        let run = Run.make(type: .morningBriefing, status: .failure, exitCode: 1,
                           logPath: URL(fileURLWithPath: "/tmp/scout-test/run.log"))
        let p = try #require(Problem(run: run))
        #expect(p.severity == .error)
        #expect(p.title == "Morning briefing: Failed")
        #expect(p.fix != nil)
        #expect(p.details?.contains("/tmp/scout-test/run.log") == true)
        #expect(p.details?.contains("Exit code: 1") == true)
    }

    @Test("ordinary skips are notes; safety skips are errors", arguments: [
        ("skippedBudget", Problem.Severity.info),
        ("skippedConcurrency", .info),
        ("skipped_redundant", .info),
        ("skipped_gates", .error),
        ("skipped_sandbox", .error),
        ("skipped_runtime", .warning),
        ("rateLimited", .warning),
        ("orphaned", .warning),
        ("timeout", .error),
    ])
    func skipSeverity(raw: String, severity: Problem.Severity) throws {
        let p = try #require(Problem(runStatus: raw, runName: "Research"))
        #expect(p.severity == severity)
        #expect(!DisplayNameTests.looksRaw(p.title), "\(p.title)")
        #expect(p.impact != nil)
    }

    @Test("failure classes map to problems; transient ones are warnings")
    func failureClasses() {
        #expect(Problem(failureClass: "oauth_expired").severity == .error)
        #expect(Problem(failureClass: "network").severity == .warning)
        // The sign-in race is retried and the login is fine: never "an error Scout doesn't recognise".
        #expect(Problem(failureClass: "auth_race").severity == .warning)
        #expect(Problem(failureClass: "auth_race").title == "Claude login was busy")
        #expect(Problem(failureClass: "rate_limited").title == "Usage limit reached")
    }

    @Test("a health finding keeps its summary and fix, and moves the raw parts to details")
    func finding() {
        let f = RunHealth.Finding(id: "runs.failing", severity: "red", since: nil,
                                  summary: "The last 3 runs failed", fix: "Sign in to Claude Code.",
                                  source: "ledger", detail: "401 Unauthorized", log: "/tmp/x.log")
        let p = Problem(finding: f)
        #expect(p.severity == .error)
        #expect(p.title == "The last 3 runs failed")
        #expect(p.fix == "Sign in to Claude Code.")
        #expect(p.details?.contains("401 Unauthorized") == true)
        #expect(p.details?.contains("runs.failing") == true)
        let note = RunHealth.Finding(id: "x", severity: "note", since: nil, summary: "s", fix: "", source: "s")
        #expect(Problem(finding: note).severity == .info)
        #expect(Problem(finding: note).fix == nil)
    }

    @Test("blank details are dropped; copy text carries every part")
    func detailsAndCopy() {
        #expect(Problem(severity: .error, title: "t", details: "  \n").details == nil)
        let p = Problem(severity: .error, title: "What", impact: "Why", fix: "Do", details: "raw")
        #expect(p.copyText == "What\n\nWhy\n\nWhat to do: Do\n\nraw")
        #expect(Problem.Severity.info < .warning && Problem.Severity.warning < .error)
    }

    @Test("a connector alert becomes a problem with that connector's fix")
    func connectorAlert() {
        let alert = ConnectorAlert(ts: Date(), level: .critical, connector: "github",
                                   reason: "`gh` returned 401", firstSeen: Date())
        let p = Problem(connectorAlert: alert)
        #expect(p.severity == .error)
        #expect(p.title == "GitHub isn't working")
        #expect(p.impact == "gh returned 401")
        #expect(p.fix?.contains("/mcp") == false)
        #expect(p.details?.contains("gh auth login") == true)
        let warn = ConnectorAlert(ts: Date(), level: .warning, connector: "mcp:claude_ai_Slack",
                                  reason: "slow", firstSeen: Date())
        #expect(Problem(connectorAlert: warn).severity == .warning)
        #expect(Problem.connectorRosterMissing(details: "snapshot not found").details == "snapshot not found")
    }

    @Test("engine problems name the affected page and keep the engine's words in details")
    func engineProblems() {
        let p = Problem.engineUnavailable("Sessions", details: "scoutctl not found")
        #expect(p.title == "Sessions couldn't reach the Scout engine")
        #expect(!p.title.contains("scoutctl"))
        #expect(p.details == "scoutctl not found")
        #expect(Problem.engineTooOld("Sessions", details: nil).severity == .warning)
    }
}

@Suite("Plain-language surfaces")
@MainActor
struct PlainLanguageSurfaceTests {

    @Test("the Sessions banner speaks plainly and keeps the engine message in details")
    func sessionsBanner() throws {
        let missing = try #require(SessionsView.problem(availability: .engineMissing,
                                                        lastError: "scoutctl not found — check that scout-plugin is installed.",
                                                        unreadable: 0))
        #expect(!missing.title.contains("scoutctl"))
        #expect(missing.details?.contains("scoutctl") == true)
        let old = try #require(SessionsView.problem(availability: .engineTooOld, lastError: nil, unreadable: 0))
        #expect(!old.title.contains("scout-plugin"))
        #expect(old.details?.contains("/scout-update") == true)
        let schema = try #require(SessionsView.problem(availability: .unsupportedSchema(9), lastError: nil, unreadable: 0))
        #expect(!schema.title.contains("schema"))
        #expect(schema.details?.contains("v9") == true)
        let unreadable = try #require(SessionsView.problem(availability: .ok, lastError: nil, unreadable: 2))
        #expect(unreadable.severity == .info)
        #expect(SessionsView.problem(availability: .ok, lastError: nil, unreadable: 0) == nil)
    }

    @Test("the Action Items engine banner says what still works")
    func actionItemsBanner() {
        let stale = ActionItemsView.environmentProblem(ActionItemsEnvironmentResult(
            ok: false, message: "Engine can't save action items.", engineTooOld: true))
        #expect(stale.title == "Action Items needs a newer Scout engine")
        #expect(stale.impact?.contains("read") == true)
        let missing = ActionItemsView.environmentProblem(ActionItemsEnvironmentResult(ok: false, message: nil))
        #expect(missing.severity == .error)
        #expect(missing.details == "scoutctl unavailable")
    }

    @Test("run search matches the words on screen, not only the engine key")
    func runSearch() {
        let run = Run.make(type: .morningBriefing, status: .rateLimited)
        #expect(SessionsListView.matches(run, "morning briefing"))
        #expect(SessionsListView.matches(run, "morningBriefing"))
        #expect(SessionsListView.matches(run, "usage limit"))
        #expect(!SessionsListView.matches(run, "dreaming"))
    }

    /// Phrases this pass removed. If one comes back, use the glossary word
    /// (Run now, schedule, Scout folder) or put the engine detail in a
    /// Problem's `details`.
    @Test("retired engine phrases stay out of the app's source")
    func retiredPhrases() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appending(path: "Scout")
        let retired = [
            "Button(\"Fire now\")", "Retry (bypass budget)", "Slot keys are immutable",
            "scoutctl schedule init", "Dreaming-generated SKILL.md", "single-fire-per-tick",
            "Removes this slot from schedule.yaml", "No upcoming runs returned by scoutctl",
            "Text(run.status.rawValue)", "Text(policy.rawValue", "type.rawValue.capitalized",
            "latest.status.rawValue", "next evening run", "Connector roster fallback warning", "Budget limit reached",
            "Another session was running", "Privacy checks didn't load", "type /mcp in a Claude Code session), then wait",
            "Close slot details", "\"Add Slot\"", "\"Edit Slot\"", "\"Delete Slot\"", "Schedule slot",
            "Pick a slot to edit", "Slots without a tag", "Slot key required", "Slot key must be",
        ]
        let files = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        var hits: [String] = []
        for case let url as URL in files where url.pathExtension == "swift" {
            let text = try String(contentsOf: url, encoding: .utf8)
            for phrase in retired where text.contains(phrase) {
                hits.append("\(url.lastPathComponent): \(phrase)")
            }
        }
        #expect(hits.isEmpty, "\(hits)")
    }
}

@Suite("Glossary")
struct GlossaryTests {

    @Test("the unavoidable terms are defined, and their other names find them", arguments: [
        ("vault", GlossaryTerm.scoutFolder),
        ("knowledge base", .knowledgeBase),
        ("slot", .schedule),
        ("delegate", .delegate),
        ("scoutctl", .engine),
    ])
    func findsTerm(query: String, term: GlossaryTerm) {
        #expect(GlossaryTerm.matching(query).contains(term))
    }

    @Test("definitions are plain sentences without engine words")
    func definitions() {
        for t in GlossaryTerm.allCases {
            #expect(t.definition.hasSuffix("."))
            #expect(!t.definition.contains("scoutctl"))
            #expect(!t.definition.contains("SKILL.md"))
            #expect(t.tooltip.hasPrefix(t.term))
        }
        #expect(GlossaryTerm.matching("").count == GlossaryTerm.allCases.count)
        #expect(GlossaryTerm.matching("zzz-no-such-term").isEmpty)
    }
}
