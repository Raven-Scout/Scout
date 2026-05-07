import Foundation

/// Run-type vocabulary for Scout sessions. Plan 5 collapsed the time-tagged
/// consolidation/dreaming variants to slot-type-aligned cases — slot keys
/// like `morning-consolidation` and `evening-consolidation` both map to
/// `.consolidation` here, and the schedule row already shows the time.
enum RunType: String, CaseIterable, Codable, Sendable {
    case morningBriefing
    case weekendBriefing
    case consolidation
    case dreaming
    case research
    case manual
}

enum RunSource: String, Codable, Sendable {
    case launchdScheduled
    case heartbeat
    case manual
    case retry
}

/// Run outcome classification consumed by the Control Center.
///
/// Exhaustive switches: `RunRow.iconName`. All other consumers
/// (`RunRow.iconColor`, `RunRow.statusColor`, `NotificationService.verb`,
/// `AppState.recomputeMenuStatus`) fall through `default` branches to
/// muted/idle/generic treatment — intentional for statuses that should
/// de-emphasize in the UI (`.orphaned`, `.skippedBudget`,
/// `.skippedConcurrency`). When adding a new case, audit those consumers
/// to decide whether `default` is still correct.
enum RunStatus: String, Codable, Sendable {
    case scheduled
    case running
    case success
    case failure
    case timeout
    case orphaned
    case skippedBudget
    case skippedConcurrency
    case rateLimited
}

extension RunType {
    /// Human-readable name without embedded times — the schedule rows already
    /// show the time, so the display label only needs to convey the kind of
    /// work ("Consolidation", "Briefing").
    var displayName: String {
        switch self {
        case .morningBriefing:    return "Morning briefing"
        case .weekendBriefing:    return "Weekend briefing"
        case .consolidation:      return "Consolidation"
        case .dreaming:           return "Dreaming"
        case .research:           return "Research"
        case .manual:             return "Manual run"
        }
    }
}

extension RunType {
    /// Map a slot key (emitted by `scoutctl schedule list-upcoming`) to a
    /// RunType. Returns nil for unknown keys so the Schedule v2 UI can skip
    /// rows it doesn't understand instead of forcing them into `.manual`.
    init?(slotKey: String) {
        switch slotKey {
        case "morning-briefing":   self = .morningBriefing
        case "weekend-briefing":   self = .weekendBriefing
        case "morning-consolidation",
             "midday-consolidation",
             "afternoon-consolidation",
             "evening-consolidation": self = .consolidation
        case "dreaming-evening",
             "dreaming-nightly",
             "dreaming-weekend-morning": self = .dreaming
        case "research":           self = .research
        default: return nil
        }
    }
}

struct Run: Identifiable, Equatable, Hashable, Sendable {
    let id: String
    let type: RunType
    let runnerScript: String
    let source: RunSource
    let scheduledAt: Date?
    let startedAt: Date
    let endedAt: Date?
    let status: RunStatus
    let exitCode: Int?
    let cost: Decimal?
    let budgetCap: Decimal?
    let logPath: URL
    let logSizeBytes: Int64
    let errorsDetected: [DetectedError]
    let commits: [Commit]
    let retryOf: Run.ID?

    static func makeId(type: RunType, startedAt: Date) -> String {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        return "\(type.rawValue)-\(iso.string(from: startedAt))"
    }

    /// Display name for the Control Center. When the typed bucket is `.manual`
    /// (the catch-all for runs that didn't match any scheduled slot), fall
    /// back to the runner script's family so the row still tells the user
    /// what kind of work happened — "Briefing (manual)" beats just "manual".
    var displayName: String {
        if type != .manual { return type.displayName }
        switch runnerScript {
        case "run-dreaming.sh": return "Dreaming (manual)"
        case "run-research.sh": return "Research (manual)"
        default:                return "Briefing (manual)"
        }
    }

    /// True when this run was triggered explicitly (Run-now) rather than by
    /// launchd. Used to surface a small "manual" badge in run rows.
    var wasManuallyTriggered: Bool {
        source == .manual || source == .retry || type == .manual
    }
}

#if DEBUG
extension Run {
    /// Test-only convenience. Fills in plausible defaults; override what you need.
    static func make(
        type: RunType = .morningBriefing,
        source: RunSource = .launchdScheduled,
        startedAt: Date = Date(),
        endedAt: Date? = nil,
        status: RunStatus = .success,
        exitCode: Int? = 0,
        cost: Decimal? = nil,
        logPath: URL = URL(fileURLWithPath: "/tmp/fake.log"),
        commits: [Commit] = [],
        retryOf: Run.ID? = nil
    ) -> Run {
        Run(
            id: makeId(type: type, startedAt: startedAt),
            type: type,
            runnerScript: "run-scout.sh",
            source: source,
            scheduledAt: nil,
            startedAt: startedAt,
            endedAt: endedAt,
            status: status,
            exitCode: exitCode,
            cost: cost,
            budgetCap: 10,
            logPath: logPath,
            logSizeBytes: 0,
            errorsDetected: [],
            commits: commits,
            retryOf: retryOf
        )
    }
}
#endif
