import Foundation

/// A scheduled fire that hasn't happened yet. Distinct from `Run` because it
/// has no log file, cost, commits, etc. — only a scheduled time and type.
///
/// `slotKey` is the schedule v2 identifier (e.g. `morning-briefing`,
/// `evening-consolidation`, or a key the owner made up in the app) emitted by
/// `scoutctl schedule list-upcoming`. It survives the `RunType` collapse so
/// the UI can fire-now precisely without trying to round-trip through the
/// (now-lossy) RunType.
struct UpcomingRun: Identifiable, Equatable, Hashable, Sendable {
    let id: String          // "{slotKey}-{scheduledAt ISO8601 UTC}"
    let slotKey: String     // e.g. "morning-briefing"
    let type: RunType
    let scheduledAt: Date

    /// What the run is called on screen: its schedule's name from the slot
    /// key, for a default schedule ("Midday consolidation", "Nightly
    /// dreaming") and one the owner made ("Late research") alike. The Runs
    /// list names a finished run from the same key (`DisplayName.ledgerRun`),
    /// so one schedule has one name in the Upcoming strip, the menu bar, the
    /// status bar and the run list. The type's name ("Consolidation") can't
    /// tell the four consolidation schedules apart.
    var displayName: String {
        DisplayName.slotKey(slotKey)
    }
}

extension RunType {
    /// The run type for a schedule, from its key when it's a default one and
    /// otherwise from its `slot_type` (`briefing`, `consolidation`, …), so a
    /// schedule made in the app still has a type (finding #17). nil only for a
    /// `slot_type` this build doesn't know.
    init?(slotKey: String, slotType: String) {
        if let known = RunType(slotKey: slotKey) {
            self = known
            return
        }
        switch SlotType(rawValue: slotType) {
        case .briefing:
            self = slotKey.localizedCaseInsensitiveContains("weekend") ? .weekendBriefing : .morningBriefing
        case .consolidation: self = .consolidation
        case .dreaming:      self = .dreaming
        case .research:      self = .research
        case .manual:        self = .manual
        case nil:            return nil
        }
    }
}
