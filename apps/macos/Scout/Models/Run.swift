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
/// Exhaustive switches: `RunRow.iconName`, `RunStatus.skipReason`,
/// `ScoutStatus.derive` and `RunNotificationPolicy`. Other consumers
/// (`RunRow.iconColor`, `RunRow.statusColor`) fall through `default`
/// branches to muted treatment — intentional for statuses that should
/// de-emphasize in the UI (`.orphaned` and the skips). When adding a new
/// case, audit those consumers to decide whether `default` is still correct.
enum RunStatus: String, Codable, Sendable, CaseIterable {
    case scheduled
    case running
    case success
    case failure
    case timeout
    case orphaned
    case skippedBudget
    case skippedConcurrency
    case rateLimited
    /// The runner skipped the run on purpose. One case per run-ledger
    /// outcome (`skipped_gates`, `skipped_sandbox`, …), so the ledger
    /// (card C8) maps onto these directly (`RunLedgerEntry.runStatus`);
    /// `RunSkipLine` reads them from the log for runs without a ledger row.
    case skippedGates
    case skippedSandbox
    case skippedRuntime
    case skippedLease
    case skippedRedundant
}

extension RunStatus {
    /// The runner decided not to run this session. A skip is a finished
    /// run, never a running or orphaned one.
    var isSkip: Bool { skipReason != nil }

    /// The runner held the run back because it couldn't run it safely: the
    /// privacy check couldn't be confirmed, or write protection couldn't
    /// start. Unlike a budget or lock skip, every later run stops the same
    /// way until the owner fixes it, so it never reads as a routine pause.
    var isSafetySkip: Bool { self == .skippedGates || self == .skippedSandbox }

    /// Why a skipped run didn't run, in the owner's words ("Over budget");
    /// nil for every status that isn't a skip. The words come from
    /// `DisplayName.skipReason`, so they match the run's status name
    /// ("Skipped: over budget") everywhere.
    var skipReason: String? {
        switch self {
        case .skippedBudget, .skippedConcurrency, .skippedGates, .skippedSandbox,
             .skippedRuntime, .skippedLease, .skippedRedundant:
            return DisplayName.skipReason(rawValue)
        case .scheduled, .running, .success, .failure, .timeout, .orphaned, .rateLimited: return nil
        }
    }
}

// `RunType.displayName` and `RunStatus.displayName` live in
// `Utilities/PlainLanguage/DisplayName.swift`, with every other engine label.

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
    /// Absolute start parsed from the log header (zone-aware). `startedAt`
    /// stays filename-derived because the run id and the Claude-session title
    /// match format it back into wall-clock text. Commit windows use this
    /// when present (#43).
    let headerStartedAt: Date?
    let status: RunStatus
    let exitCode: Int?
    let cost: Decimal?
    let budgetCap: Decimal?
    let logPath: URL
    let logSizeBytes: Int64
    let errorsDetected: [DetectedError]
    let commits: [Commit]
    let retryOf: Run.ID?
    /// The model the runner passed to claude, from the log's
    /// `=== model: … ===` line; nil for runs logged before per-slot models.
    var model: String? = nil
    /// The schedule this run ran for (`evening-consolidation`, a slot the owner
    /// made in the app), from the run ledger's `slot`. nil when the run has no
    /// ledger row: it's still going, it's older than the ledger's window, or an
    /// older engine wrote it. The log itself never names the slot, and the run
    /// type can't recover it (several slots share one type), so nothing guesses.
    var slotKey: String? = nil
    /// What started the run, from the run ledger's `trigger`: `schedule`,
    /// `heartbeat`, `manual`, or `event` (a connector-event trigger, whose
    /// "slot" is the skill it ran, not a schedule). nil without a ledger row,
    /// or from a runner older than the field.
    var trigger: String? = nil
    /// Why the run failed, from the run ledger's `failure_class` (`timeout`,
    /// `rate_limited`, `auth_race`, …; `ok` for a run that didn't fail). nil
    /// without a ledger row. The engine classifies the log; the app never
    /// guesses a class from log text of its own.
    var failureClass: String? = nil

    /// The slot "Run again" fires: this run's own, or nil when there isn't one
    /// to fire. A run started outside the schedule (`manual`,
    /// `dreaming-manual`, `research-manual`, or any `event` run, whose slot is
    /// a skill name that `fire-now` doesn't know or, worse, that a schedule
    /// shares) or one the ledger couldn't name (`unknown`) has none.
    var rerunSlotKey: String? {
        guard startedBySchedule,
              let key = slotKey?.trimmingCharacters(in: .whitespaces), !key.isEmpty,
              key != "unknown", key != "manual", !key.hasSuffix("-manual")
        else { return nil }
        return key
    }

    /// The ledger says a schedule (or the heartbeat, or a person forcing a
    /// schedule) started this run. An older row with no `trigger` counts:
    /// those runners never recorded event runs apart.
    var startedBySchedule: Bool {
        switch trigger {
        case nil, "schedule", "heartbeat", "manual": return true
        default: return false
        }
    }

    /// `<type>-<ISO 8601 start>`, plus `-<pid>` for a run whose log has the
    /// seconds+PID name (a later run in the same minute). A minute-named log
    /// keeps the id it always had, so feedback files' `run_id` still match.
    static func makeId(type: RunType, startedAt: Date, pid: Int? = nil) -> String {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        let base = "\(type.rawValue)-\(iso.string(from: startedAt))"
        return pid.map { "\(base)-\($0)" } ?? base
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

    /// Copy with a different `status`. Used by the stale-running reconciler
    /// (`SessionLogService.resolveStaleRunning`) to demote a stuck `.running`
    /// run to `.orphaned` without rebuilding every field by hand.
    func with(status: RunStatus) -> Run {
        Run(
            id: id, type: type, runnerScript: runnerScript, source: source,
            scheduledAt: scheduledAt, startedAt: startedAt, endedAt: endedAt,
            headerStartedAt: headerStartedAt, status: status, exitCode: exitCode, cost: cost, budgetCap: budgetCap,
            logPath: logPath, logSizeBytes: logSizeBytes,
            errorsDetected: errorsDetected, commits: commits, retryOf: retryOf,
            model: model, slotKey: slotKey, trigger: trigger, failureClass: failureClass
        )
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
        retryOf: Run.ID? = nil,
        runnerScript: String? = nil,
        headerStartedAt: Date? = nil,
        model: String? = nil,
        slotKey: String? = nil,
        trigger: String? = nil,
        failureClass: String? = nil
    ) -> Run {
        Run(
            id: makeId(type: type, startedAt: startedAt),
            type: type,
            runnerScript: runnerScript ?? {
                switch type {
                case .dreaming: return "run-dreaming.sh"
                case .research: return "run-research.sh"
                default:        return "run-scout.sh"
                }
            }(),
            source: source,
            scheduledAt: nil,
            startedAt: startedAt,
            endedAt: endedAt,
            headerStartedAt: headerStartedAt,
            status: status,
            exitCode: exitCode,
            cost: cost,
            budgetCap: 10,
            logPath: logPath,
            logSizeBytes: 0,
            errorsDetected: [],
            commits: commits,
            retryOf: retryOf,
            model: model,
            slotKey: slotKey,
            trigger: trigger,
            failureClass: failureClass
        )
    }
}
#endif
