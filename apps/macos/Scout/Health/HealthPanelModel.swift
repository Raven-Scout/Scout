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
        /// What to do, for a run that needs the owner (from its `Problem`).
        let fix: String?
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
    /// The engine's own problems and warnings (doctor install checks), grouped by kind,
    /// so the page lists what Settings counts (UI audit R1).
    let engineIssues: [HealthSummary.Issue]
    /// Engine warnings the owner marked reviewed (UI audit R13): listed apart, counted nowhere.
    let reviewedIssues: [HealthSummary.Issue]
    /// Run-health notes, then the doctor's informational notes.
    let everythingElse: [String]
    let runs: [RunLine]

    /// `doctor` adds the engine's own warnings and its notes; the run findings come from the ledger.
    init(ledger: RunLedger?, doctor: DoctorReport? = nil, filter: Filter = .all) {
        let health = ledger?.health
        let shared = HealthSummary(doctor: doctor, runHealth: .some(health))
        severity = health.map { _ in shared.severity }
        headline = Self.headline(runs: health?.severity, shared: shared)
        let findings = health?.findings ?? []
        var grouped: [RunOutcomeDisplay.Group: [RunHealth.Finding]] = [:]
        for finding in findings { grouped[.init(severity: finding.severity), default: []].append(finding) }
        groups = [RunOutcomeDisplay.Group.red, .yellow].compactMap { g in
            grouped[g].map { FindingGroup(group: g, findings: $0) }
        }
        engineIssues = shared.issues.filter { $0.source == .install && $0.severity != .note }
        reviewedIssues = shared.reviewed
        everythingElse = (grouped[.note] ?? []).map(\.summary) + (doctor?.notes ?? [])
        let entries = ledger?.runs ?? []
        summary = Self.summary(entries)
        runs = entries
            .filter { filter == .all || $0.needsAttention }
            .map(Self.line)
    }

    /// The runs' verdict, then the count every health surface shows: "Scheduled runs need a look · 2 warnings".
    /// Healthy runs beside engine warnings say both, never a bare "healthy" (R1). When the engine's
    /// own problem makes the overall verdict worse than the runs' (`HealthSummary.overall`), the
    /// headline names the engine, as Settings and the Doctor window do: never "Scheduled runs are
    /// healthy · 1 problem" beside Settings' "Needs attention" (T4 review).
    static func headline(runs: DoctorReport.Severity?, shared: HealthSummary) -> String {
        guard let runs else { return RunOutcomeDisplay.overall(nil) }
        let count = shared.attentionLabel.map { " · \($0)" } ?? ""
        if shared.overall(doctor: runs) == .red, runs != .red {
            return "Scout's engine needs attention" + count
        }
        return RunOutcomeDisplay.overall(runs) + count
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
        let agentName: String? = e.agentRuntime.map { $0 == "claude-code" ? "Claude Code" : DisplayName.humanize($0) }
        let problem = e.problem
        let agent = [agentName, e.model].compactMap { $0 }.joined(separator: " · ")
        return RunLine(
            id: e.id,
            title: DisplayName.slotKey(e.slot),
            finished: e.finishedAt,
            outcome: e.displayStatus,
            reason: problem?.impact,
            fix: tone == .problem || tone == .warning ? problem?.fix : nil,
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
