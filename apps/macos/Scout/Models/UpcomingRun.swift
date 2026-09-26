import Foundation

/// A scheduled fire that hasn't happened yet. Distinct from `Run` because it
/// has no log file, cost, commits, etc. — only a scheduled time and type.
///
/// `slotKey` is the schedule v2 identifier (e.g. `morning-briefing`,
/// `evening-consolidation`) emitted by `scoutctl schedule list-upcoming`.
/// It survives the `RunType` collapse so the UI can fire-now precisely
/// without trying to round-trip through the (now-lossy) RunType.
struct UpcomingRun: Identifiable, Equatable, Hashable, Sendable {
    let id: String          // "{slotKey}-{scheduledAt ISO8601 UTC}"
    let slotKey: String     // e.g. "morning-briefing"
    let type: RunType
    let scheduledAt: Date
}
