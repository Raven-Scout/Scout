import Foundation

/// Everything the Health page shows, worked out from the run ledger and the
/// doctor's notes. Pure, so it is tested without a view.
nonisolated struct HealthPanelModel: Equatable, Sendable {
    nonisolated struct FindingGroup: Equatable, Sendable, Identifiable {
        let group: RunOutcomeDisplay.Group
        let findings: [RunHealth.Finding]
        var id: Int { group.rawValue }
    }

    /// One line per run, already in the owner's words.
    nonisolated struct RunLine: Equatable, Sendable, Identifiable {
        let id: String
        let title: String
        let finished: Date
        let outcome: String
        let reason: String?
        /// "6 min", "1 h 30 min"; nil when the row has no length (a skip).
        let length: String?
        /// "Claude Code · sonnet"; nil when the row names neither.
        let agent: String?
        let protection: String?
        let integrity: String?
        let tone: Tone
        /// The run's log file name under `.scout-logs/`.
        let log: String?
        let resetsAt: Date?
    }

    enum Tone: Equatable, Sendable { case ok, skipped, warning, problem }

    enum Filter: String, CaseIterable, Identifiable, Sendable {
        case all, problems
        var id: String { rawValue }
        var title: String { self == .all ? "All runs" : "Needs attention" }
    }

    let severity: DoctorReport.Severity?
    let headline: String
    let summary: String
    /// Red and yellow findings, worst first (notes go in `everythingElse`).
    let groups: [FindingGroup]
    /// Run-health notes, then the doctor's informational notes.
    let everythingElse: [String]
    let runs: [RunLine]

    init(ledger: RunLedger?, doctorNotes: [String] = [], filter: Filter = .all) {
        let health = ledger?.health
        severity = health?.severity
        headline = RunOutcomeDisplay.overall(health?.severity)
        let findings = health?.findings ?? []
        var grouped: [RunOutcomeDisplay.Group: [RunHealth.Finding]] = [:]
        for finding in findings { grouped[.init(severity: finding.severity), default: []].append(finding) }
        groups = [RunOutcomeDisplay.Group.red, .yellow].compactMap { g in
            grouped[g].map { FindingGroup(group: g, findings: $0) }
        }
        everythingElse = (grouped[.note] ?? []).map(\.summary) + doctorNotes
        let entries = ledger?.runs ?? []
        summary = Self.summary(entries)
        runs = entries
            .filter { filter == .all || $0.needsAttention }
            .map(Self.line)
    }

    static func summary(_ entries: [RunLedgerEntry]) -> String {
        guard !entries.isEmpty else { return "No scheduled runs in the last two weeks." }
        let finished = entries.filter { $0.outcome == "ok" }.count
        let skipped = entries.filter(\.isSkip).count
        let failed = entries.count - finished - skipped
        var parts = ["\(finished) finished"]
        if failed > 0 { parts.append("\(failed) failed") }
        if skipped > 0 { parts.append("\(skipped) skipped") }
        let runs = entries.count == 1 ? "1 run" : "\(entries.count) runs"
        return "\(runs) in the last two weeks: " + parts.joined(separator: ", ") + "."
    }

    static func line(_ e: RunLedgerEntry) -> RunLine {
        let tone: Tone = {
            if e.outcome == "ok" {
                return RunOutcomeDisplay.integrityIsConcern(e.integrity) || RunOutcomeDisplay.sandboxIsConcern(e.sandbox)
                    ? .warning : .ok
            }
            if e.isSkip { return e.needsAttention ? .warning : .skipped }
            return .problem
        }()
        let agentName: String? = e.agentRuntime.map { $0 == "claude-code" ? "Claude Code" : RunOutcomeDisplay.humanize($0) }
        let agent = [agentName, e.model].compactMap { $0 }.joined(separator: " · ")
        return RunLine(
            id: e.id,
            title: RunOutcomeDisplay.humanize(e.slot),
            finished: e.finishedAt,
            outcome: RunOutcomeDisplay.outcome(e.outcome, failureClass: e.failureClass),
            reason: RunOutcomeDisplay.reason(outcome: e.outcome, failureClass: e.failureClass),
            length: e.isSkip ? nil : length(e.awakeSeconds ?? e.durationSeconds),
            agent: agent.isEmpty ? nil : agent,
            // Only worth a line when it isn't the normal "on" / "clean".
            protection: RunOutcomeDisplay.sandboxIsConcern(e.sandbox)
                ? RunOutcomeDisplay.sandbox(e.sandbox, source: e.sandboxSource) : nil,
            integrity: RunOutcomeDisplay.integrityIsConcern(e.integrity) ? RunOutcomeDisplay.integrity(e.integrity) : nil,
            tone: tone,
            log: e.log,
            resetsAt: e.resetsAt
        )
    }

    static func length(_ seconds: Int?) -> String? {
        guard let seconds, seconds > 0 else { return nil }
        let minutes = max(1, Int((Double(seconds) / 60).rounded()))
        if minutes < 60 { return "\(minutes) min" }
        let h = minutes / 60, m = minutes % 60
        return m == 0 ? "\(h) h" : "\(h) h \(m) min"
    }
}
