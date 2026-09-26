import Foundation

/// Filter state for the Schedules tab. `.all` is the default; `.type(...)`
/// filters the master list to a single slot type.
enum SchedulesFilterMode: Hashable {
    case all
    case type(SlotType)

    /// Apply the filter to a slot list. Pure; no allocation when `.all`.
    func apply(to slots: [Slot]) -> [Slot] {
        switch self {
        case .all:
            return slots
        case .type(let target):
            return slots.filter { $0.type == target }
        }
    }

    /// Count slots of the given type in the source list. Used by the
    /// per-type filter chips' badge counts (so empty types can hide their
    /// chip rather than render `0`).
    static func count(of type: SlotType, in slots: [Slot]) -> Int {
        slots.lazy.filter { $0.type == type }.count
    }
}
