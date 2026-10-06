import Foundation

/// The run family a vault commit subject claims. Scout's sessions prefix their
/// commits `<family> [..]: …`, and that prefix is more reliable than the
/// `RunType` derived from a log filename's local hour, which mis-buckets
/// scheduled runs once the machine's timezone changes. Declaration order is
/// match order: longest token first, so "weekend briefing …" never reads as
/// "briefing". (#43)
nonisolated enum CommitFamily: String, CaseIterable, Sendable {
    case weekendBriefing = "weekend briefing"
    case consolidation
    case briefing
    case dreaming
    case research

    static func of(subject: String) -> CommitFamily? {
        for family in allCases where subject.hasPrefix(family.rawValue) {
            let next = subject.dropFirst(family.rawValue.count).first
            if next == nil || next == " " || next == ":" || next == "[" { return family }
        }
        return nil
    }

    /// The runner script whose runs make this family's commits — stable,
    /// unlike the hour-bucketed `RunType`.
    var runnerScript: String {
        switch self {
        case .weekendBriefing, .briefing, .consolidation: return "run-scout.sh"
        case .dreaming: return "run-dreaming.sh"
        case .research: return "run-research.sh"
        }
    }

    var displayName: String {
        switch self {
        case .weekendBriefing: return "Weekend briefing"
        case .briefing:        return "Briefing"
        case .consolidation:   return "Consolidation"
        case .dreaming:        return "Dreaming"
        case .research:        return "Research"
        }
    }
}

extension Run {
    /// The span in which this run's own commits land — the single definition
    /// shared by `SessionLogService.commits(for:)` and `CommitRunLinker`.
    /// Starts 30 s before the (zone-aware, when known) start; ends 5 min after
    /// the finish so wind-down commits count. A run with no finish marker
    /// extends to `now` while running, else stops at its orphan cutoff so it
    /// can't claim commits forever. Never inverted.
    nonisolated func commitWindow(now: Date) -> ClosedRange<Date> {
        let anchor = headerStartedAt ?? startedAt
        let lower = anchor.addingTimeInterval(-30)
        let naturalEnd = endedAt
            ?? (status == .running ? now : anchor.addingTimeInterval(type.orphanAfter))
        let upper = max(naturalEnd, lower).addingTimeInterval(5 * 60)
        return lower...upper
    }

    /// True when the commit's subject names a family this run's runner makes.
    /// Concurrency/budget skips never start Claude, so they make no commits —
    /// and since they start after the run that blocked them, they'd otherwise
    /// win the reverse link's latest-start tie-break.
    nonisolated func claims(_ commit: Commit) -> Bool {
        guard status != .skippedConcurrency, status != .skippedBudget else { return false }
        return CommitFamily.of(subject: commit.subject)?.runnerScript == runnerScript
    }
}

/// Maps a commit back to the run that made it — the reverse of
/// `SessionLogService.commits(for:)`. Overlapping candidates resolve to the
/// latest start. Unclaimed subjects (`app: …`, interactive sessions) never
/// link; the caller labels them instead. The item's patch is shown either
/// way, so a miss never hides evidence (#43).
nonisolated enum CommitRunLinker {
    static func run(for commit: Commit, in runs: [Run], now: Date) -> Run? {
        runs
            .filter { $0.claims(commit) && $0.commitWindow(now: now).contains(commit.timestamp) }
            .max { ($0.headerStartedAt ?? $0.startedAt) < ($1.headerStartedAt ?? $1.startedAt) }
    }
}
