import Foundation

/// Something that went wrong, said the way the owner needs it: what happened,
/// why it matters, what to do — with the engine's own wording kept in
/// `details` for support, behind a "Details" disclosure and "Copy details".
///
/// A plain value (no closures), so services can publish it and tests can
/// compare it. Buttons are passed to `ProblemView` separately. Health
/// findings, run failures, skipped runs, engine errors and write failures all
/// render through this one type (card C4 of the 2026-11-16 app UI audit).
nonisolated struct Problem: Equatable, Hashable, Sendable, Identifiable {
    enum Severity: String, Sendable, Comparable {
        /// Worth knowing; nothing is broken (a note, a skipped run that will
        /// retry by itself).
        case info
        /// Something needs the owner's attention soon.
        case warning
        /// Something is broken until the owner acts.
        case error

        static func < (a: Severity, b: Severity) -> Bool {
            let order: [Severity] = [.info, .warning, .error]
            return order.firstIndex(of: a)! < order.firstIndex(of: b)!
        }
    }

    var severity: Severity
    /// What happened, in one short sentence.
    var title: String
    /// Why it matters to the owner. Optional when the title says it.
    var impact: String?
    /// What to do about it. nil when there is nothing to do.
    var fix: String?
    /// The engine's own words: stderr, a log path, a command. Shown only
    /// behind "Details" and copied by "Copy details".
    var details: String?

    var id: String { "\(severity.rawValue)|\(title)|\(details ?? "")" }

    init(severity: Severity, title: String, impact: String? = nil,
         fix: String? = nil, details: String? = nil) {
        self.severity = severity
        self.title = title
        self.impact = impact
        self.fix = fix
        let trimmed = details?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.details = (trimmed?.isEmpty ?? true) ? nil : trimmed
    }

    /// The text "Copy details" puts on the pasteboard: every field, so a
    /// support request carries the plain sentence and the raw detail.
    var copyText: String {
        [title, impact, fix.map { "What to do: \($0)" }, details]
            .compactMap { $0 }
            .joined(separator: "\n\n")
    }
}

// MARK: - Factories

extension Problem {
    /// A run that failed with a run-ledger `failure_class`.
    /// Failure classes that clear by themselves (a usage limit, a busy
    /// login, the network, a busy folder): a warning, not an error. Keys are
    /// `DisplayName.normalized`.
    static let softFailureClasses: Set<String> = ["ratelimited", "authrace", "network", "gitlock", "leaseheld"]

    static func isSoftFailureClass(_ raw: String) -> Bool {
        softFailureClasses.contains(DisplayName.normalized(raw))
    }

    init(failureClass: String, details: String? = nil) {
        let e = DisplayName.failureClass(failureClass)
        let soft = Self.isSoftFailureClass(failureClass)
        let combined = [details, e.details].compactMap { $0 }.joined(separator: "\n")
        self.init(severity: soft ? .warning : .error, title: e.title,
                  impact: e.why, fix: e.fix, details: combined)
    }

    /// A connector alert from the engine's connector-health report, with the
    /// fix that connector needs (claude.ai, `gh`, the Chrome extension…).
    init(connectorAlert alert: ConnectorAlert) {
        let name = DisplayName.connector(alert.connector)
        let fix = DisplayName.connectorFix(alert.connector)
        let detail = [fix.details, "Connector: \(alert.connector)"].compactMap { $0 }.joined(separator: "\n")
        self.init(severity: alert.level == .critical ? .error : .warning,
                  title: alert.level == .critical ? "\(name) isn't working" : "\(name) has a problem",
                  impact: alert.reason.replacingOccurrences(of: "`", with: ""),
                  fix: fix.fix, details: detail)
    }

    /// The app couldn't read the engine's connector roster and fell back to
    /// its built-in list. `details` is the service's reason.
    static func connectorRosterMissing(details: String?) -> Problem {
        Problem(severity: .warning,
                title: "Scout couldn't read its list of connectors",
                impact: "The health table uses the built-in list and may miss some connectors.",
                fix: "Updating Scout usually fixes this.",
                details: details)
    }

    /// The problem a finished run shows, or nil when it ended well (or is
    /// still going). Skips are `.info` — Scout tries again at the next time —
    /// except the safety skips, which stop every run until fixed.
    init?(run: Run) {
        let details = "Log: \(run.logPath.path)" + (run.exitCode.map { "\nExit code: \($0)" } ?? "")
        self.init(runStatus: run.status.rawValue, runName: run.displayName, details: details)
    }

    /// Same as `init?(run:)` from a raw status or run-ledger outcome, so a
    /// status the app doesn't model yet still gets a readable problem.
    init?(runStatus raw: String, runName: String, details: String? = nil) {
        let key = DisplayName.normalized(raw)
        guard !["scheduled", "running", "success", "ok"].contains(key) else { return nil }
        let status = DisplayName.runStatus(raw)
        let reason = DisplayName.runStatusReason(raw)
        let severity: Severity
        let fix: String?
        switch key {
        case "skippedgates", "skippedsandbox":
            severity = .error
            fix = "Open Settings ▸ Engine to see what needs attention."
        case "skippedruntime", "skippedlease":
            severity = .warning
            fix = "Open Settings ▸ Engine to see what needs attention."
        case let k where k.hasPrefix("skipped"):
            severity = .info
            fix = nil
        case "ratelimited":
            severity = .warning
            fix = nil
        case "orphaned":
            severity = .warning
            // The app gives up on a run at its type's cutoff (20 min to 2 h); the engine
            // waits 2 h without progress before stopping one, so say both.
            fix = "Open the run's log to see how far it got. If it is stuck, the next scheduled run stops it once it has gone 2 hours without progress."
        default:
            severity = .error
            fix = "Open the run's log for details, or run it again."
        }
        self.init(severity: severity, title: "\(runName): \(status)",
                  impact: reason, fix: fix, details: details)
    }

    /// A finding from the engine's health check (`bootstrap doctor --json`).
    init(finding: RunHealth.Finding) {
        let severity: Severity = switch DisplayName.normalized(finding.severity) {
        case "red", "error": .error
        case "yellow", "warn", "warning": .warning
        default: .info
        }
        let detail = [finding.detail, finding.log.map { "Log: \($0)" }, "Finding: \(finding.id)"]
            .compactMap { $0 }.joined(separator: "\n")
        self.init(severity: severity, title: finding.summary,
                  fix: finding.fix.isEmpty ? nil : finding.fix, details: detail)
    }

    /// The app couldn't run the engine, or couldn't read what it said.
    /// `what` names the part of the app that is affected ("Sessions").
    static func engineUnavailable(_ what: String, details: String?) -> Problem {
        Problem(severity: .error,
                title: "\(what) couldn't reach the Scout engine",
                impact: "\(what) can't update until the engine is working.",
                fix: "Open Settings ▸ Engine and choose Check now. If it still fails, copy the details when asking for help.",
                details: details)
    }

    /// The engine is older than this part of the app needs.
    static func engineTooOld(_ what: String, details: String?) -> Problem {
        Problem(severity: .warning,
                title: "\(what) needs a newer Scout engine",
                impact: "\(what) stays empty until the engine is updated.",
                fix: "Update the engine from Settings ▸ Updates, then come back.",
                details: details)
    }
}
