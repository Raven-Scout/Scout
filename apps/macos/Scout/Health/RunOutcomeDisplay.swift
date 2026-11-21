import Foundation

/// The Health panel's own wording: write protection (the sandbox), the
/// integrity check, finding groups and the overall headline. Outcomes and
/// failure classes are `DisplayName`'s (card C4) and problems are `Problem`'s;
/// this adds only what the run ledger has that nothing else shows. Engine
/// words ("sandbox", "seatbelt", "gates", "lease") never reach the owner.
nonisolated enum RunOutcomeDisplay {

    // MARK: One run's status, everywhere (UI audit R10, #21)

    /// The run failed: the run ledger's `failed` outcome, whatever its
    /// failure class (a timeout and a usage limit included). Skips, runs still
    /// going and runs that stopped without a last line are not failures. The
    /// Today tile, the Signals card, the activity heatmap and the Health page
    /// all count with this one rule.
    static func isFailure(_ status: RunStatus) -> Bool {
        switch status {
        case .failure, .timeout, .rateLimited: return true
        case .scheduled, .running, .success, .orphaned, .skippedBudget, .skippedConcurrency,
             .skippedGates, .skippedSandbox, .skippedRuntime, .skippedLease, .skippedRedundant:
            return false
        }
    }

    /// The colour family a run's status reads in, the same one the menu-bar
    /// panel and the status bar use for the latest run (`ScoutStatus`): a
    /// failure, a run that didn't finish and a skip that stops every later
    /// run until fixed are red; a routine skip is amber, never a red ✗.
    static func tone(_ status: RunStatus) -> ScoutStatus.Tone {
        switch status {
        case .success: return .ok
        case .running: return .active
        case .scheduled: return .neutral
        case .failure, .timeout, .rateLimited, .orphaned: return .err
        case .skippedGates, .skippedSandbox, .skippedRuntime: return .err
        case .skippedBudget, .skippedConcurrency, .skippedLease, .skippedRedundant: return .warn
        }
    }

    /// The small symbol beside a run's status.
    static func symbol(_ status: RunStatus) -> String {
        switch status {
        case .success: return "checkmark"
        case .running: return "circle.fill"
        case .scheduled: return "clock"
        case .failure, .timeout, .rateLimited, .orphaned: return "xmark"
        case .skippedGates, .skippedSandbox: return "shield.slash"
        case .skippedRuntime: return "nosign"
        case .skippedBudget, .skippedConcurrency, .skippedLease, .skippedRedundant: return "pause"
        }
    }

    /// How many of `runs` failed.
    static func failedCount(_ runs: [Run]) -> Int {
        runs.filter { isFailure($0.status) }.count
    }

    /// The one-line reason a failed run failed, from its run-ledger failure
    /// class ("Claude login was busy"); nil for a run that didn't fail, or
    /// failed before the ledger could say why.
    static func failureReason(_ run: Run) -> String? {
        failureClass(run).map { DisplayName.failureClass($0).title }
    }

    /// A failed run's failure class: the ledger's, or before its row lands
    /// the one its status already implies (`timeout`, `rate_limited`). nil
    /// for a run that didn't fail, or failed without saying why yet.
    static func failureClass(_ run: Run) -> String? {
        guard isFailure(run.status) else { return nil }
        if let fc = run.failureClass?.trimmingCharacters(in: .whitespaces), !fc.isEmpty, fc != "ok" {
            return fc
        }
        switch run.status {
        case .timeout: return "timeout"
        case .rateLimited: return "rate_limited"
        default: return nil
        }
    }

    // MARK: Write protection and the integrity check

    /// nil when there is nothing to say (no value from an older runner).
    static func sandbox(_ raw: String?, source: String? = nil) -> String? {
        switch raw {
        case "enforced": return "Write protection on"
        case "off":
            switch source {
            case "switch": return "Write protection turned off in Settings"
            case "env": return "Write protection turned off for this Mac's runs"
            default: return "Write protection off"
            }
        case "unavailable": return "Write protection isn't available on this Mac"
        case "conflict": return "Write protection clashed with another setting"
        case "failed": return "Write protection couldn't start"
        case nil: return nil
        case let other?: return "Write protection: \(DisplayName.humanize(other).lowercased())"
        }
    }

    static func sandboxIsConcern(_ raw: String?) -> Bool {
        guard let raw else { return false }
        return raw != "enforced"
    }

    static func integrity(_ raw: String?) -> String? {
        switch raw {
        case "clean": return "No protected files changed"
        case "restored": return "Scout put a protected file back"
        case "unrestored": return "A protected file changed and couldn't be put back"
        case "error": return "Couldn't check protected files"
        case "unchecked": return "Protected files weren't checked"
        case nil: return nil
        case let other?: return "Protected files: \(DisplayName.humanize(other).lowercased())"
        }
    }

    static func integrityIsConcern(_ raw: String?) -> Bool {
        guard let raw else { return false }
        return raw != "clean"
    }

    // MARK: Findings

    /// Run-health severities, worst first. An unknown severity sorts with notes.
    enum Group: Int, CaseIterable, Comparable, Sendable {
        case red, yellow, note
        static func < (a: Group, b: Group) -> Bool { a.rawValue < b.rawValue }
        init(severity: String) {
            switch severity {
            case "red": self = .red
            case "yellow": self = .yellow
            default: self = .note
            }
        }
        var title: String {
            switch self {
            case .red: return "Needs attention"
            case .yellow: return "Worth a look"
            case .note: return "Everything else"
            }
        }
    }

    static func overall(_ severity: DoctorReport.Severity?) -> String {
        switch severity {
        case .green: return "Scheduled runs are healthy"
        case .yellow: return "Scheduled runs need a look"
        case .red: return "Scheduled runs need attention"
        case nil: return "Run health unknown"
        }
    }

    // MARK: Findings about one schedule

    /// The schedule a finding names: `slot.stale:<key>` → ("stale", key).
    /// nil for any other finding, or an id with an empty kind or key.
    static func slotFinding(_ id: String) -> (kind: String, key: String)? {
        guard id.hasPrefix("slot."), let colon = id.firstIndex(of: ":") else { return nil }
        let kind = String(id[id.index(id.startIndex, offsetBy: 5)..<colon])
        let key = String(id[id.index(after: colon)...])
        guard !kind.isEmpty, !key.isEmpty else { return nil }
        return (kind, key)
    }

    /// A warning line under one schedule on the Schedules page.
    struct SlotWarning: Equatable, Sendable, Identifiable {
        let text: String
        let severity: Problem.Severity
        /// The finding's id, for `Identifiable`.
        let id: String
    }

    /// What one `slot.*` finding says about its schedule, without the
    /// schedule's name (the row already shows it). `now` ages the finding.
    static func slotLine(_ finding: RunHealth.Finding, now: Date = Date()) -> String {
        guard let (kind, key) = slotFinding(finding.id) else { return finding.summary }
        let since = RunLedgerTime.parse(finding.since)
        switch kind {
        case "stale":
            guard let since else { return "Hasn't run lately" }
            return "Hasn't run for \(age(now.timeIntervalSince(since)))"
        case "never_fired":
            return "Hasn't run yet"
        case "fired_silent":
            // `since` is the last fire: usually the session's end, not its start.
            guard let since else { return "Ran, but Scout has no record of how the run ended" }
            return "Ran \(age(now.timeIntervalSince(since))) ago, but Scout has no record of how the run ended"
        default:
            // A kind this build doesn't know: the engine's own sentence, with
            // the schedule's name in place of its key.
            return finding.summary.replacingOccurrences(of: key, with: DisplayName.slotKey(key))
        }
    }

    /// Every schedule's warning lines, keyed by slot key, worst first.
    /// Notes stay on the Health page.
    static func slotWarnings(_ health: RunHealth?, now: Date = Date()) -> [String: [SlotWarning]] {
        var out: [String: [SlotWarning]] = [:]
        for finding in health?.findings ?? [] {
            guard let (_, key) = slotFinding(finding.id) else { continue }
            let group = Group(severity: finding.severity)
            guard group != .note else { continue }
            out[key, default: []].append(SlotWarning(text: slotLine(finding, now: now),
                                                     severity: group == .red ? .error : .warning,
                                                     id: finding.id))
        }
        return out.mapValues { $0.sorted { $0.severity > $1.severity } }
    }

    /// "40 min", "5 hours", "3 days".
    static func age(_ seconds: TimeInterval) -> String {
        let s = max(0, seconds)
        if s < 3600 { return "\(max(1, Int(s / 60))) min" }
        if s < 48 * 3600 {
            let h = Int(s / 3600)
            return h == 1 ? "1 hour" : "\(h) hours"
        }
        return "\(Int(s / 86400)) days"
    }

    // MARK: The Control Center banner

    /// How long a wake-sensitive finding must persist before the banner
    /// shows it. At wake, launchd fires the missed heartbeat and the schedule
    /// tick together, and the first read can see a scheduler that only looks
    /// stalled. Mirrors the engine notifier's `GRACE` (`health_notify.py`).
    static let bannerGrace: TimeInterval = 20 * 60

    /// Findings the banner holds back for `bannerGrace`: the engine
    /// notifier's `GRACED_PREFIXES`.
    static func isGraced(_ id: String) -> Bool {
        id.hasPrefix("scheduler.stalled") || id.hasPrefix("slot.stale:")
    }

    /// The red run-health problem the Control Center leads with, or nil
    /// unless run health is red. The worst finding speaks; the rest are
    /// counted and left to the Health page. A graced finding is left out
    /// until `firstSeen` (`RunLedgerService.firstSeen`) says it has persisted
    /// for `bannerGrace`; while only graced reds make health red, there is
    /// no banner. The Health page and its sidebar dot don't wait.
    static func bannerProblem(_ health: RunHealth?, firstSeen: [String: Date] = [:],
                              now: Date = Date()) -> Problem? {
        guard let health, health.severity == .red else { return nil }
        let actionable = health.findings.filter { Group(severity: $0.severity) != .note }
        func held(_ f: RunHealth.Finding) -> Bool {
            guard isGraced(f.id) else { return false }
            guard let seen = firstSeen[f.id] else { return true }
            return now.timeIntervalSince(seen) < bannerGrace
        }
        let shown = actionable.filter { !held($0) }
        let anyHeld = shown.count < actionable.count
        guard let worst = shown.first(where: { Group(severity: $0.severity) == .red })
                ?? (anyHeld ? nil : shown.first)
        else {
            if anyHeld { return nil }
            return Problem(severity: .error, title: overall(.red),
                           fix: "Open the Health page to see what went wrong.")
        }
        var problem = plainProblem(worst, now: now)
        problem.severity = .error
        let more = shown.count - 1
        if more > 0 {
            problem.impact = more == 1 ? "1 more problem is on the Health page."
                                       : "\(more) more problems are on the Health page."
        }
        return problem
    }

    /// `Problem(finding:)` in plain words for the findings the engine words
    /// in its own terms (raw times, `scoutctl` commands): the scheduler and
    /// the three schedule kinds. The engine's sentence and fix stay under
    /// Details. Any other finding is `Problem(finding:)` as it is.
    static func plainProblem(_ finding: RunHealth.Finding, now: Date = Date()) -> Problem {
        var problem = Problem(finding: finding)
        let awakeFix = "Make sure the Mac is awake at schedule times, then choose Check now in Settings ▸ Engine."
        let plain: (title: String, fix: String)?
        if finding.id == "scheduler.stalled" {
            let title = RunLedgerTime.parse(finding.since).map {
                "Scout's scheduler hasn't run for \(age(now.timeIntervalSince($0)))"
            } ?? "Scout's scheduler has never run"
            plain = (title, "Choose Check now in Settings ▸ Engine. If the scheduler still hasn't run, reinstall it: the command is under Details.")
        } else if let (kind, key) = slotFinding(finding.id) {
            let name = DisplayName.slotKey(key)
            let line = slotLine(finding, now: now)
            switch kind {
            case "stale", "never_fired":
                plain = ("\(name): \(line)", awakeFix)
            case "fired_silent":
                plain = ("\(name): \(line)",
                         "Open the run's log from the Health page to see how far it got. If it keeps happening, update Scout.")
            default:
                // A kind this build doesn't know: `slotLine` is the engine's
                // sentence with the name already in it, and the engine's fix
                // is the only one there is.
                problem.title = line
                return problem
            }
        } else {
            plain = nil
        }
        guard let plain else { return problem }
        problem.title = plain.title
        problem.fix = plain.fix
        problem.details = [finding.summary, finding.fix.isEmpty ? nil : "Engine fix: \(finding.fix)", problem.details]
            .compactMap { $0 }.joined(separator: "\n")
        return problem
    }
}

extension RunLedgerEntry {
    /// "Finished", "Stopped: took too long", "Skipped: privacy checks didn't
    /// load", in `DisplayName`'s words. A failed row says how it failed when
    /// the failure class has its own status (timeout, usage limit); an outcome
    /// this build doesn't know reads as "Failed", never as the raw value.
    var displayStatus: String {
        let known = DisplayName.runStatuses
        if outcome == "failed", let failureClass, known[DisplayName.normalized(failureClass)] != nil {
            return DisplayName.runStatus(failureClass)
        }
        return known[DisplayName.normalized(outcome)] != nil ? DisplayName.runStatus(outcome) : DisplayName.runStatus("failed")
    }

    /// What went wrong and what to do, or nil for a run that finished. A
    /// failure (or a skip with its own cause, like the privacy check's) is
    /// explained by its failure class; any other skip by its status.
    var problem: Problem? {
        guard outcome != "ok" else { return nil }
        let details = log.map { "Log: .scout-logs/\($0)" }
        if let failureClass, failureClass != "ok" {
            return Problem(failureClass: failureClass, details: details)
        }
        if isSkip {
            return Problem(runStatus: outcome, runName: DisplayName.ledgerRun(slot: slot, mode: mode, runner: runner, startedAt: startedAt) ?? DisplayName.slotKey(slot), details: details)
        }
        return Problem(failureClass: "unknown", details: details)
    }
}
