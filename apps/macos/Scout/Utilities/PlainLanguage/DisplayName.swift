import Foundation

/// The one place engine identifiers become words the owner reads.
///
/// Run types, run statuses, run-ledger outcomes, failure classes, slot keys,
/// slot types, catch-up policies, connector keys and health levels all reach
/// the UI as engine strings (`morningBriefing`, `rateLimited`,
/// `skipped_gates`, `oauth_expired`, `dreaming-nightly`, `mcp:claude_ai_Slack`,
/// `yellow`). Views ask this module for the label instead of printing
/// `rawValue` or writing their own strings, so one concept has one name on
/// every page (card C4 of the 2026-11-16 app UI audit).
///
/// Every lookup is keyed by the raw string, normalized (case, `_`, `-`, `.`
/// and spaces ignored), so the camelCase Swift enum value and the snake_case
/// value the engine writes to its run ledger share one entry. A value the
/// table doesn't know falls back to `humanize`, never to the raw key: a new
/// engine value reads as "Skipped token ceiling", not `skipped_token_ceiling`.
nonisolated enum DisplayName {

    // MARK: - Fallback

    /// A readable sentence-case label for any engine identifier.
    /// `morningBriefing` → "Morning briefing", `skipped_token_ceiling` →
    /// "Skipped token ceiling", `slot.never-ran` → "Slot never ran". Words
    /// already in capitals (`API`, `KB`) stay that way. Empty input → "Unknown".
    static func humanize(_ raw: String) -> String {
        var words: [String] = []
        var current = ""
        let chars = Array(raw)
        for (i, ch) in chars.enumerated() {
            if ch == "_" || ch == "-" || ch == "." || ch == ":" || ch == "/" || ch.isWhitespace {
                if !current.isEmpty { words.append(current); current = "" }
                continue
            }
            if ch.isUppercase, !current.isEmpty {
                // Split "morningBriefing" before the B; keep "API" together but
                // split "APIError" before the E (an upper followed by a lower).
                let prevUpper = chars[i - 1].isUppercase
                let nextLower = i + 1 < chars.count && chars[i + 1].isLowercase
                if !prevUpper || nextLower {
                    words.append(current); current = ""
                }
            }
            current.append(ch)
        }
        if !current.isEmpty { words.append(current) }
        guard !words.isEmpty else { return "Unknown" }
        let cased = words.enumerated().map { i, w -> String in
            let isAcronym = w.count > 1 && w == w.uppercased() && w.contains(where: \.isLetter)
            if isAcronym { return w }
            let lower = w.lowercased()
            return i == 0 ? lower.prefix(1).uppercased() + lower.dropFirst() : lower
        }
        return cased.joined(separator: " ")
    }

    /// Lookup key shared by camelCase and snake_case spellings.
    static func normalized(_ raw: String) -> String {
        raw.lowercased().filter { $0.isLetter || $0.isNumber }
    }

    private static func lookup(_ table: [String: String], _ raw: String) -> String {
        table[normalized(raw)] ?? humanize(raw)
    }

    // MARK: - Run types and slots

    static func runType(_ type: RunType) -> String {
        lookup(runTypes, type.rawValue)
    }

    private static let runTypes: [String: String] = [
        "morningbriefing": "Morning briefing",
        "weekendbriefing": "Weekend briefing",
        "consolidation":   "Consolidation",
        "dreaming":        "Dreaming",
        "research":        "Research",
        "manual":          "Manual run",
    ]

    /// A schedule's name from its slot key: `morning-consolidation` →
    /// "Morning consolidation", `dreaming-nightly` → "Nightly dreaming".
    /// Keys the owner made up in the app read as written, minus the dashes.
    static func slotKey(_ key: String) -> String {
        lookup(slotKeys, key)
    }

    private static let slotKeys: [String: String] = [
        "morningbriefing":        "Morning briefing",
        "weekendbriefing":        "Weekend briefing",
        "morningconsolidation":   "Morning consolidation",
        "middayconsolidation":    "Midday consolidation",
        "afternoonconsolidation": "Afternoon consolidation",
        "eveningconsolidation":   "Evening consolidation",
        "dreamingevening":        "Evening dreaming",
        "dreamingnightly":        "Nightly dreaming",
        "dreamingweekendmorning": "Weekend morning dreaming",
        "research":               "Research",
    ]

    static func slotType(_ type: SlotType) -> String {
        lookup(slotTypes, type.rawValue)
    }

    private static let slotTypes: [String: String] = [
        "briefing":      "Briefing",
        "consolidation": "Consolidation",
        "dreaming":      "Dreaming",
        "research":      "Research",
        "manual":        "Manual",
    ]

    /// What a schedule does when the Mac was asleep at its time.
    static func onMiss(_ policy: OnMissPolicy) -> PlainExplanation {
        switch policy {
        case .fire:
            return PlainExplanation(
                title: "Run late",
                why: "Runs as soon as the Mac is awake, if that is within the catch-up window.")
        case .skip:
            return PlainExplanation(
                title: "Skip",
                why: "Skips the missed time and waits for the next one.")
        case .collapse:
            return PlainExplanation(
                title: "Run once",
                why: "Runs once for all the times it missed, within the catch-up window.")
        }
    }

    // MARK: - Run status and outcome

    /// The status of a run, from the app's `RunStatus` (`rateLimited`) or
    /// the engine's run-ledger outcome (`skipped_gates`, `ok`, `failed`).
    static func runStatus(_ raw: String) -> String {
        lookup(runStatuses, raw)
    }

    static func runStatus(_ status: RunStatus) -> String {
        runStatus(status.rawValue)
    }

    /// One sentence on why a run ended up in this status, for tooltips and the
    /// problem component. nil for statuses that need no explanation.
    static func runStatusReason(_ raw: String) -> String? {
        runStatusReasons[normalized(raw)]
    }

    /// Keys every status the app models today plus the run-ledger outcomes the
    /// engine writes (`plugin/templates/scripts/run-outcome.sh.tmpl`), so a
    /// skip status added to `RunStatus` later already has its name.
    static let runStatuses: [String: String] = [
        "scheduled":           "Scheduled",
        "running":             "Running",
        "success":             "Finished",
        "ok":                  "Finished",
        "failure":             "Failed",
        "failed":              "Failed",
        "timeout":             "Stopped: took too long",
        "orphaned":            "Didn't finish",
        "ratelimited":         "Paused: usage limit",
        "skippedbudget":       "Skipped: over budget",
        "skippedconcurrency":  "Skipped: another run was going",
        "skippedlock":         "Skipped: another run was going",
        "skippedredundant":    "Skipped: already done",
        "skippedgates":        "Skipped: privacy checks didn't load",
        "skippedsandbox":      "Skipped: couldn't protect this run",
        "skippedruntime":      "Skipped: agent not allowed",
        "skippedlease":        "Skipped: another app was writing",
        "skippedtokenceiling": "Skipped: daily usage cap",
    ]

    private static let runStatusReasons: [String: String] = [
        "timeout":             "The run reached its time limit and Scout stopped it.",
        "orphaned":            "The run stopped without writing its last line, so Scout can't tell how it ended.",
        "ratelimited":         "Your Claude plan's usage limit was reached. Scout tries again after it resets.",
        "skippedbudget":       "Running it would have gone over the budget you set.",
        "skippedconcurrency":  "Another Scout run was still going, so this one waited for the next time.",
        "skippedlock":         "Another Scout run was still going, so this one waited for the next time.",
        "skippedredundant":    "An earlier run already covered this time.",
        "skippedgates":        "Scout's privacy checks didn't load for this version of Claude Code, so the run didn't start.",
        "skippedsandbox":      "Scout couldn't turn on write protection for this run, so it didn't start.",
        "skippedruntime":      "The agent this schedule uses isn't allowed to run, or it's past its daily limit.",
        "skippedlease":        "Another app was writing to the Scout folder, so this run waited.",
        "skippedtokenceiling": "The agent reached its daily usage cap.",
    ]

    // MARK: - Failure classes

    /// What a run-ledger `failure_class` means, why it matters and what to do.
    static func failureClass(_ raw: String) -> PlainExplanation {
        failureClasses[normalized(raw)]
            ?? PlainExplanation(title: humanize(raw), why: "The run stopped with an error.",
                                fix: "Open the run's log for details.")
    }

    private static let failureClasses: [String: PlainExplanation] = [
        "oauthexpired": PlainExplanation(
            title: "Signed out of Claude",
            why: "Scheduled runs can't start until Claude Code is signed in again.",
            fix: "Open Claude Code in a terminal and sign in."),
        "network": PlainExplanation(
            title: "Network problem",
            why: "The run couldn't reach Claude or a connected service.",
            fix: "Check your internet connection. Scout tries again at the next scheduled time."),
        "gitlock": PlainExplanation(
            title: "Scout folder was busy",
            why: "Another program was saving changes to the Scout folder at the same time.",
            fix: "This usually clears by itself. If it keeps happening, close other apps that save to the folder."),
        "ratelimited": PlainExplanation(
            title: "Usage limit reached",
            why: "Your Claude plan's usage limit was reached, so the run stopped.",
            fix: "Nothing to do: Scout tries again after the limit resets."),
        "budget": PlainExplanation(
            title: "Over budget",
            why: "The run used up the budget set for it.",
            fix: "Raise the budget in Settings ▸ Budget, or leave it: the next run starts with a fresh budget."),
        "timeout": PlainExplanation(
            title: "Took too long",
            why: "The run reached its time limit and Scout stopped it.",
            fix: "If this keeps happening, give this schedule a longer time limit."),
        "leaseheld": PlainExplanation(
            title: "Another app was writing",
            why: "Another app was writing to the Scout folder, so the run waited.",
            fix: "Nothing to do: the next run tries again."),
        "leaseunavailable": PlainExplanation(
            title: "Couldn't check who is writing",
            why: "Scout couldn't check whether another app was writing to the Scout folder, so the run waited.",
            fix: "If this keeps happening, check Settings ▸ Engine."),
        "gatesunsafeendpoint": PlainExplanation(
            title: "Privacy checks didn't load",
            why: "Scout's privacy checks didn't load, so the run didn't start.",
            fix: "Update Claude Code and Scout, then check Settings ▸ Engine."),
        "gatesunsafelauncher": PlainExplanation(
            title: "Privacy checks didn't load",
            why: "Scout's privacy checks didn't load, so the run didn't start.",
            fix: "Update Claude Code and Scout, then check Settings ▸ Engine."),
        "unknown": PlainExplanation(
            title: "Unrecognised error",
            why: "The run stopped with an error Scout doesn't recognise.",
            fix: "Open the run's log for details."),
    ]

    // MARK: - Connectors, alerts and health

    /// A connector's name: `mcp:claude_ai_Slack` → "Slack". Unknown keys lose
    /// their MCP prefix and read as words.
    static func connector(_ key: String) -> String {
        if let known = connectors[key] { return known }
        let stripped = key
            .replacingOccurrences(of: "mcp:claude_ai_", with: "")
            .replacingOccurrences(of: "mcp:plugin_", with: "")
            .replacingOccurrences(of: "mcp:", with: "")
            .replacingOccurrences(of: "notify:", with: "")
            .replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
        let title = stripped.split(separator: " ")
            .map { $0.prefix(1).uppercased() + $0.dropFirst() }
            .joined(separator: " ")
        return title.isEmpty ? "Unknown" : title
    }

    /// Keep in sync with the roster in `plugin/engine/scout/connectors.yaml`.
    private static let connectors: [String: String] = [
        "mcp:claude_ai_Slack":           "Slack",
        "mcp:claude_ai_Linear":          "Linear",
        "mcp:claude_ai_Gmail":           "Gmail",
        "mcp:claude_ai_Google_Calendar": "Calendar",
        "mcp:claude_ai_Granola":         "Granola",
        "mcp:fathom":                    "Fathom",
        "mcp:claude_ai_Google_Drive":    "Drive",
        "github":                        "GitHub",
        "mcp:claude-in-chrome":          "Chrome",
        "mcp:whatsapp-mcp":              "WhatsApp",
        "notify:telegram":               "Telegram",
        "cc-session":                    "Claude Code",
    ]

    /// A connector alert level (`CRITICAL`, `WARNING`) in sentence case.
    static func alertLevel(_ raw: String) -> String {
        lookup(["critical": "Not working", "warning": "Warning"], raw)
    }

    /// An engine health level: doctor's `green`/`yellow`/`red`, a finding's
    /// `note`, or `ok`/`warn`/`error`.
    static func health(_ raw: String) -> String {
        lookup(healthLevels, raw)
    }

    private static let healthLevels: [String: String] = [
        "green":  "Healthy",
        "ok":     "Healthy",
        "yellow": "Needs attention",
        "warn":   "Needs attention",
        "warning": "Needs attention",
        "red":    "Not working",
        "error":  "Not working",
        "note":   "For your information",
    ]
}

/// A label plus the sentence that explains it, and what to do when it needs
/// doing. Built by `DisplayName` and consumed by `Problem`.
nonisolated struct PlainExplanation: Equatable, Sendable {
    let title: String
    let why: String
    var fix: String? = nil
}

// MARK: - Model conveniences

extension RunType {
    /// Human-readable name without embedded times — the schedule rows already
    /// show the time, so the display label only needs to convey the kind of
    /// work ("Consolidation", "Briefing").
    var displayName: String { DisplayName.runType(self) }
}

extension RunStatus {
    /// "Paused: usage limit", not `rateLimited`. See `DisplayName.runStatus`.
    var displayName: String { DisplayName.runStatus(self) }
    /// Why the run is in this status, or nil when the name says it all.
    var reason: String? { DisplayName.runStatusReason(rawValue) }
}

extension SlotType {
    var displayName: String { DisplayName.slotType(self) }
}

extension OnMissPolicy {
    var displayName: String { DisplayName.onMiss(self).title }
    var explanation: String { DisplayName.onMiss(self).why }
}

extension Slot {
    /// The schedule's name, from its key.
    var displayName: String { DisplayName.slotKey(key) }
}

extension ConnectorAlert.Level {
    var displayName: String { DisplayName.alertLevel(rawValue) }
}
