import Foundation

/// One answer to "what is Scout doing, and is it all right?", shared by the
/// menu-bar icon, the menu-bar panel's status card and the window's status
/// bar, so the three can't disagree (card C3).
///
/// The engine comes first: with no engine, a broken one, or a health check
/// that can't be read, the last run says nothing trustworthy, so the status
/// never claims "Ready" then. Only a usable engine lets the latest run speak.
nonisolated struct ScoutStatus: Equatable, Sendable {
    nonisolated enum Kind: Equatable, Sendable, CaseIterable {
        /// The first engine health check hasn't answered yet.
        case checking
        /// No engine, or an app-managed engine whose vault isn't set up.
        case needsSetup
        /// A broken engine, an unreadable or red health check, or failing runs.
        case needsAttention
        /// A Claude usage limit is holding the runs; they resume by themselves.
        case paused
        case running
        case lastFailed
        /// A routine skip: over budget, another run or app writing, already done.
        case skipped
        /// The last run was held back for safety (`RunStatus.isSafetySkip`):
        /// every run stops the same way until the owner fixes it.
        case heldForSafety
        /// The last run's agent isn't allowed to run (`skippedRuntime`).
        case agentNotAllowed
        case ready
    }

    nonisolated enum Tone: Equatable, Sendable { case ok, warn, err, active, neutral }

    /// The one thing to do about this status.
    nonisolated enum Fix: Equatable, Sendable {
        /// Finish setup: the main window shows the setup flow.
        case setUp
        /// Settings ▸ Engine has the details and the repair.
        case openSettings

        var title: String { self == .setUp ? "Set up Scout…" : "Open Settings" }
    }

    let kind: Kind
    let title: String
    let detail: String
    let tone: Tone
    var fix: Fix? = nil

    static func derive(engine: EngineState, doctor: DoctorReport?, doctorError: String?,
                       latestRun: Run?, now: Date = Date()) -> ScoutStatus {
        if let problem = engineStatus(engine: engine, doctor: doctor, doctorError: doctorError) { return problem }
        guard let doctor else {
            return ScoutStatus(kind: .checking, title: "Checking Scout…", detail: "Looking at the engine", tone: .neutral)
        }
        if let runs = runHealthStatus(doctor.runHealth, now: now) { return runs }
        var status = runStatus(latestRun, now: now)
        // Warnings don't stop the runs, but "Ready" in green would hide them. The count is the
        // one Settings, the Doctor window and the Health page show, and the detail says why (R1).
        let summary = HealthSummary(doctor: doctor)
        if status.kind == .ready, doctor.severity == .yellow || summary.attentionCount > 0 {
            let n = max(summary.attentionCount, 1)
            status = ScoutStatus(kind: .ready, title: "Ready · \(n) warning\(n == 1 ? "" : "s")",
                                 detail: summary.reason ?? status.detail,
                                 tone: .warn, fix: .openSettings)
        }
        return status
    }

    // MARK: - Engine

    private static func engineStatus(engine: EngineState, doctor: DoctorReport?, doctorError: String?) -> ScoutStatus? {
        switch engine {
        case .notInstalled:
            return ScoutStatus(kind: .needsSetup, title: "Scout isn't set up",
                               detail: "No engine found on this Mac", tone: .warn, fix: .setUp)
        case .managed(_, vaultBootstrapped: false):
            return ScoutStatus(kind: .needsSetup, title: "Finish setting up Scout",
                               detail: "The engine is installed; the vault isn't set up yet", tone: .warn, fix: .setUp)
        case .broken:
            return ScoutStatus(kind: .needsAttention, title: "Scout's engine is broken",
                               detail: "Its files are missing or moved", tone: .err,
                               fix: engine.isBrokenOutsideApp ? .openSettings : .setUp)
        case .managed, .external:
            break
        }
        if doctor == nil, doctorError != nil {
            return ScoutStatus(kind: .needsAttention, title: "Can't read Scout's engine",
                               detail: "Its health check didn't answer", tone: .err, fix: .openSettings)
        }
        if let doctor, doctor.severity == .red {
            let n = max(doctor.errors.count, 1)
            return ScoutStatus(kind: .needsAttention, title: "Scout needs attention",
                               detail: "\(n) problem\(n == 1 ? "" : "s") with the engine", tone: .err, fix: .openSettings)
        }
        return nil
    }

    private static func runHealthStatus(_ runHealth: RunHealth?, now: Date) -> ScoutStatus? {
        guard let runHealth, let finding = runHealth.firstActionable else { return nil }
        if finding.id == "runs.paused" {
            let lifts = finding.until.flatMap { ISO8601DateFormatter().date(from: $0) }
            let detail = lifts.map { "Claude usage limit · resumes \(relative($0, now: now))" } ?? "Claude usage limit"
            return ScoutStatus(kind: .paused, title: "Runs paused", detail: detail, tone: .warn, fix: .openSettings)
        }
        guard runHealth.severity == .red else { return nil }
        return ScoutStatus(kind: .needsAttention, title: "Scheduled runs need attention",
                           detail: finding.summary, tone: .err, fix: .openSettings)
    }

    // MARK: - Latest run

    private static func runStatus(_ latest: Run?, now: Date) -> ScoutStatus {
        guard let latest else {
            return ScoutStatus(kind: .ready, title: "Ready", detail: "No runs yet", tone: .ok)
        }
        let name = latest.displayName
        switch latest.status {
        case .running:
            return ScoutStatus(kind: .running, title: "Running \(name)",
                               detail: "Started \(relative(latest.startedAt, now: now))", tone: .active)
        case .failure:
            return failed(name, "failed")
        case .timeout:
            return failed(name, "ran out of time")
        case .rateLimited:
            return failed(name, "hit a usage limit")
        case .orphaned:
            return failed(name, "stopped without finishing")
        case .skippedBudget, .skippedConcurrency, .skippedGates, .skippedSandbox, .skippedRuntime,
             .skippedLease, .skippedRedundant:
            // Safety skips mean the run couldn't happen safely: that's a
            // problem to fix, and it gets its own icon and words. Budget,
            // lock and lease skips are routine.
            let reason = latest.status.skipReason ?? "skipped"
            if latest.status.isSafetySkip {
                return ScoutStatus(kind: .heldForSafety, title: "Run held back for safety",
                                   detail: "\(name) · \(reason)", tone: .err, fix: .openSettings)
            }
            // Not routine either: every run on that agent stops until its
            // permission is changed in Settings ▸ Engine.
            if latest.status == .skippedRuntime {
                return ScoutStatus(kind: .agentNotAllowed, title: "Run skipped: agent not allowed",
                                   detail: "\(name) · \(reason)", tone: .err, fix: .openSettings)
            }
            return ScoutStatus(kind: .skipped, title: "Last run skipped",
                               detail: "\(name) · \(reason)", tone: .warn)
        case .scheduled, .success:
            let when = latest.endedAt ?? latest.startedAt
            return ScoutStatus(kind: .ready, title: "Ready",
                               detail: "Last: \(name) · \(relative(when, now: now))", tone: .ok)
        }
    }

    private static func failed(_ name: String, _ what: String) -> ScoutStatus {
        ScoutStatus(kind: .lastFailed, title: "Last run needs attention", detail: "\(name) \(what)", tone: .err)
    }

    private static func relative(_ date: Date, now: Date) -> String {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .full
        f.dateTimeStyle = .named
        return f.localizedString(for: date, relativeTo: now)
    }
}
