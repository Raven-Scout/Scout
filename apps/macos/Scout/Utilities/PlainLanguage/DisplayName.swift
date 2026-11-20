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
/// engine value reads as "Skipped disk full", not `skipped_disk_full`.
nonisolated enum DisplayName {

    // MARK: - Fallback

    /// A readable sentence-case label for any engine identifier.
    /// `morningBriefing` → "Morning briefing", `skipped_disk_full` →
    /// "Skipped disk full", `slot.never-ran` → "Slot never ran". Words
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
    /// Keys the owner made up in the app read as written, minus the dashes —
    /// unless that reads the same as a default schedule's name (a user's
    /// `nightly-dreaming` beside the default `dreaming-nightly`), in which
    /// case the key is added so the two stay tell-apart-able.
    static func slotKey(_ key: String) -> String {
        if let known = slotKeys[normalized(key)] { return known }
        let name = humanize(key)
        return defaultSlotNames.contains(name) ? "\(name) (\(key))" : name
    }

    private static let defaultSlotNames = Set(slotKeys.values)

    /// A run's name from its run-ledger row (`scoutctl runs --json`): its
    /// schedule's name from `slot` (`overnight-research` → "Overnight
    /// research"), or from `mode` when the slot is missing, `unknown`, or not
    /// shaped like a key the engine writes. A run started by hand reads
    /// "Dreaming (manual)" (`dreaming-manual`, or a bare `manual` from the
    /// dreaming runner). A bare `manual` from run-scout.sh (`/scout-briefing`,
    /// `/scout-consolidation`) is a briefing or a consolidation by the day and
    /// hour, as the session itself decides, so it is named from `startedAt`
    /// the same way the log-name guess does ("Consolidation (manual)"), or
    /// "Manual run" without one. nil when the row names nothing, so the caller
    /// falls back to its own guess. Control Center and the Health page both
    /// name runs with this (card T2).
    static func ledgerRun(slot: String?, mode: String?, runner: String? = nil, startedAt: Date? = nil) -> String? {
        let key = [slot, mode]
            .compactMap { $0?.trimmingCharacters(in: .whitespaces) }
            .first { $0 != "unknown" && isLedgerKey($0) }
        guard let key else { return nil }
        if key == "manual" {
            switch runner {
            case "dreaming": return "Dreaming (manual)"
            case "research": return "Research (manual)"
            case nil, "scout":
                guard let startedAt else { return "Manual run" }
                return "\(runType(SessionLogService.deriveType(runner: "scout", date: startedAt))) (manual)"
            default:         return "Manual run"
            }
        }
        if key.hasSuffix("-manual") {
            let family = String(key.dropLast("-manual".count))
            return family.isEmpty ? "Manual run" : "\(humanize(family)) (manual)"
        }
        return slotKey(key)
    }

    /// The engine's slot/mode shape, `[A-Za-z0-9][A-Za-z0-9._-]{0,80}`. A
    /// ledger value outside it is not named from (defence in depth: the
    /// engine already refuses such keys).
    static func isLedgerKey(_ s: String) -> Bool {
        guard let first = s.unicodeScalars.first, s.unicodeScalars.count <= 81 else { return false }
        let alnum: (Unicode.Scalar) -> Bool = { $0.isASCII && CharacterSet.alphanumerics.contains($0) }
        return alnum(first) && s.unicodeScalars.allSatisfy { alnum($0) || $0 == "." || $0 == "_" || $0 == "-" }
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

    /// What a schedule does when the Mac was asleep at its time. Mirrors
    /// `_apply_miss_rules` in `plugin/engine/scout/scripts/schedule_tick.py`:
    /// since #193 `skip` fires late inside the catch-up window exactly like
    /// `fire`, and `collapse` runs only the latest missed schedule of its kind.
    static func onMiss(_ policy: OnMissPolicy) -> PlainExplanation {
        switch policy {
        case .fire:
            return PlainExplanation(
                title: "Run late",
                why: "Runs late if the Mac wakes within the catch-up window; otherwise waits for the next time.")
        case .skip:
            return PlainExplanation(
                title: "Skip",
                why: "Runs late if the Mac wakes within the catch-up window; otherwise waits for the next time. Today this works the same as Run late.")
        case .collapse:
            return PlainExplanation(
                title: "Latest only",
                why: "If several schedules of this kind that also use Latest only were missed, only the latest one runs, and only within the catch-up window.")
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

    /// The reason half of a skip's name, as a phrase that stands alone:
    /// `skippedBudget` → "Over budget" (its name is "Skipped: over budget").
    /// nil for anything that isn't a skip.
    static func skipReason(_ raw: String) -> String? {
        let name = runStatus(raw)
        let prefix = "Skipped: "
        guard name.hasPrefix(prefix) else { return nil }
        let reason = name.dropFirst(prefix.count)
        return reason.prefix(1).uppercased() + reason.dropFirst()
    }

    /// One sentence on why a run ended up in this status, for tooltips and the
    /// problem component. nil for statuses that need no explanation.
    static func runStatusReason(_ raw: String) -> String? {
        runStatusReasons[normalized(raw)]
    }

    /// Keys every status the app models today (`skippedConcurrency` is the
    /// app's own, from the run log) plus the run-ledger outcomes the engine
    /// writes (`SKIP_OUTCOMES` in `plugin/engine/scout/health.py`, written by
    /// `plugin/templates/scripts/run-outcome.sh.tmpl`), so a skip status added
    /// to `RunStatus` later already has its name. A runtime past its daily
    /// token ceiling is recorded as `skipped_budget`.
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
        "skippedgates":        "Skipped: privacy checks not confirmed",
        "skippedsandbox":      "Skipped: couldn't protect this run",
        "skippedruntime":      "Skipped: agent not allowed",
        "skippedlease":        "Skipped: another app was writing",
    ]

    private static let runStatusReasons: [String: String] = [
        "timeout":             "The run reached its time limit and Scout stopped it.",
        "orphaned":            "The run never wrote its last line, so Scout can't tell how it ended. It stopped showing signs of work, or a later run of the same kind has started since.",
        "ratelimited":         "Your Claude plan's usage limit was reached. Scout tries again after it resets.",
        "skippedbudget":       "Running it would have gone over the budget you set, or the agent's daily usage cap.",
        "skippedconcurrency":  "Another Scout run was still going, so this one waited for the next time.",
        "skippedlock":         "Another Scout run was still going, so this one waited for the next time.",
        "skippedredundant":    "An earlier run already covered this time.",
        "skippedgates":        "Scout couldn't confirm its privacy checks would work for this run, so it didn't start.",
        "skippedsandbox":      "Scout couldn't turn on write protection for this run, so it didn't start.",
        "skippedruntime":      "The agent this schedule uses isn't allowed to run.",
        "skippedlease":        "Another app was writing to the Scout folder, so this run waited.",
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
        "authrace": PlainExplanation(
            title: "Claude login was busy",
            why: "Another Claude Code window was refreshing your login when the run started. Your login is fine.",
            fix: "Nothing to do: Scout tries again a minute later, and at the next scheduled time."),
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
        // Mirrors `_UNSAFE_ADVICE` in plugin/engine/scout/health.py.
        "gatesunsafeendpoint": PlainExplanation(
            title: "Privacy checks couldn't run safely",
            why: "One of your Claude settings names a server the privacy check can't control on this Mac, so the run didn't start.",
            fix: "Remove or rename that setting in your Claude settings. Copy the details to see how to find which one.",
            details: "Run `scoutctl gates probe` to see which setting; remove or rename it in your Claude settings' env, or run Scout where the check's network sandbox is available (macOS). Runs resume once the probe passes."),
        "gatesunsafelauncher": PlainExplanation(
            title: "Privacy checks couldn't run safely",
            why: "Scout starts Claude through a launcher script (such as a mise or asdf shim) that the privacy check can't see through, so the run didn't start.",
            fix: "Point Scout at the real Claude program instead of the launcher. Copy the details for the setting to change.",
            details: "Set connectors.inputs.claude_bin in scout-config.yaml to the real claude binary (for mise: `mise which claude`), then run /scout-update. Check with `scoutctl gates probe`; runs resume once it passes."),
        "unknown": PlainExplanation(
            title: "Unrecognised error",
            why: "The run stopped with an error Scout doesn't recognise.",
            fix: "Open the run's log for details."),
    ]

    // MARK: - Projects

    /// Names for a set of projects (folders) that can be told apart: a name
    /// only one project has reads as written; projects sharing a name
    /// (two "Scout" lanes for two folders) each get the shortest trailing
    /// part of their folder path that differs, home written as `~` —
    /// "Scout (~/Scout)" beside "Scout (code/Scout)". The `(…)` suffix is
    /// the same shape `slotKey` uses for a clashing schedule name. Names
    /// compare without case, and paths without trailing slashes. Two keys
    /// for one folder (`/a/Scout`, `/a/Scout/`) can't be told apart, so
    /// they show the folder's whole path, home still written as `~`. A key
    /// that isn't a path is its own suffix.
    static func projectNames(_ projects: [(key: String, name: String)], home: String) -> [String: String] {
        var firstName: [String: String] = [:]
        var keys: [String] = []
        for project in projects where firstName[project.key] == nil {
            firstName[project.key] = project.name
            keys.append(project.key)
        }
        let groups = Dictionary(grouping: keys) { (firstName[$0] ?? "").lowercased() }
        var result: [String: String] = [:]
        for group in groups.values {
            guard group.count > 1 else {
                result[group[0]] = firstName[group[0]]
                continue
            }
            let shown = group.map { abbreviatingHome(trimmingTrailingSlashes($0), home: home) }
            let parts = shown.map(pathComponents)
            let deepest = parts.map(\.count).max() ?? 1
            var depth = 1
            func trail(_ p: [String]) -> String { p.suffix(depth).joined(separator: "/") }
            while depth < deepest, Set(parts.map(trail)).count < group.count { depth += 1 }
            let unique = Set(parts.map(trail)).count == group.count
            for (i, key) in group.enumerated() {
                result[key] = "\(firstName[key] ?? key) (\(unique ? trail(parts[i]) : shown[i]))"
            }
        }
        return result
    }

    private static func trimmingTrailingSlashes(_ path: String) -> String {
        var trimmed = Substring(path)
        while trimmed.count > 1, trimmed.hasSuffix("/") { trimmed = trimmed.dropLast() }
        return String(trimmed)
    }

    private static func abbreviatingHome(_ path: String, home: String) -> String {
        let home = trimmingTrailingSlashes(home)
        guard !home.isEmpty else { return path }
        if path == home { return "~" }
        if path.hasPrefix(home + "/") { return "~" + path.dropFirst(home.count) }
        return path
    }

    private static func pathComponents(_ path: String) -> [String] {
        let parts = path.split(separator: "/").map(String.init)
        return parts.isEmpty ? [path] : parts
    }

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

    /// The `display_name` values in `plugin/engine/scout/connectors.yaml`,
    /// without their parenthesised notes ("GitHub (gh CLI)" → "GitHub"), which
    /// would crowd the health table. Keep in sync with that roster.
    private static let connectors: [String: String] = [
        "mcp:claude_ai_Slack":           "Slack",
        "mcp:claude_ai_Linear":          "Linear",
        "mcp:claude_ai_Gmail":           "Gmail",
        "mcp:claude_ai_Google_Calendar": "Google Calendar",
        "mcp:claude_ai_Granola":         "Granola",
        "mcp:fathom":                    "Fathom",
        "mcp:claude_ai_Google_Drive":    "Google Drive",
        "github":                        "GitHub",
        "mcp:claude-in-chrome":          "Chrome",
        "mcp:whatsapp-mcp":              "WhatsApp",
        "notify:telegram":               "Telegram",
        "cc-session":                    "Claude Code",
    ]

    /// How to fix a connector that stopped working, in plain words, with the
    /// roster's own `remediation.first_fix` (commands and all) as `details`.
    /// The connectors snapshot doesn't carry `remediation` yet, so this
    /// mirrors `plugin/engine/scout/connectors.yaml`; keep the two in sync.
    static func connectorFix(_ key: String) -> PlainExplanation {
        let name = connector(key)
        let claudeAI = "Open claude.ai, go to Settings ▸ Connectors and reconnect \(name). Scout picks it up at the next run."
        switch key {
        case "mcp:fathom":
            return PlainExplanation(title: name, why: "", fix: claudeAI,
                                    details: "Reconnect Fathom at https://claude.ai/settings/connectors — Fathom MCP tokens periodically expire.")
        case "github":
            return PlainExplanation(
                title: name, why: "",
                fix: "Sign in to GitHub again from Terminal with the GitHub command-line tool (copy the details for the command).",
                details: "Run `gh auth status`; if expired, `gh auth login` to re-auth. Token lives in macOS keychain.")
        case "mcp:claude-in-chrome":
            return PlainExplanation(
                title: name, why: "",
                fix: "Make sure Chrome is open with the Claude in Chrome extension turned on. If the Mac was asleep, waking it is enough.",
                details: "Check the extension at chrome://extensions.")
        case "mcp:whatsapp-mcp":
            return PlainExplanation(
                title: name, why: "",
                fix: "The WhatsApp bridge on this Mac may have stopped. Restart it (copy the details for the commands).",
                details: "launchctl list | grep com.scout.whatsapp-bridge\nlaunchctl kickstart -k gui/$UID/com.scout.whatsapp-bridge")
        case "notify:telegram":
            return PlainExplanation(
                title: name, why: "",
                fix: "Check that Scout's Telegram bot token is still saved on this Mac (copy the details for where).",
                details: "Check ~/.scout-secrets/telegram-bot-token exists (mode 600); test with `scoutctl notify telegram --tier info --body 'health check'`.")
        default:
            if key.hasPrefix("mcp:claude_ai_") {
                return PlainExplanation(title: name, why: "", fix: claudeAI,
                                        details: "https://claude.ai/settings/connectors")
            }
            if key.hasPrefix("mcp:") {
                return PlainExplanation(
                    title: name, why: "",
                    fix: "Reconnect \(name) in Claude Code (type /mcp in a Claude Code session). Scout picks it up at the next run.")
            }
            return PlainExplanation(
                title: name, why: "",
                fix: "Check that \(name) is set up and signed in on this Mac. Scout tries again at the next run.")
        }
    }

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
    /// The engine-level how-to (commands, setting names) for Details.
    var details: String? = nil
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
