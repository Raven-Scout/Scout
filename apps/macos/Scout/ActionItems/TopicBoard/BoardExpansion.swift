import Foundation

/// Which board groups are open, stored as one newline-separated string so it
/// fits `@AppStorage`. Groups are collapsed unless listed.
nonisolated enum BoardExpansion {
    static func contains(_ key: String, in raw: String) -> Bool {
        keys(raw).contains(key)
    }

    static func setting(_ key: String, open: Bool, in raw: String) -> String {
        var keys = keys(raw).filter { $0 != key }
        if open { keys.append(key) }
        return keys.joined(separator: "\n")
    }

    /// Whether a group shows open. Unfiltered, the saved `raw` decides. With a
    /// legend filter on, groups start open so the matching steps show, but a
    /// group closed under that filter stays closed (audit #45: the filter used
    /// to force every group open and the chevrons did nothing).
    /// `closedUnderFilter` holds those; the board clears it when the filter
    /// changes, and never writes it to `raw`.
    static func isOpen(_ key: String, in raw: String, filtering: Bool, closedUnderFilter: Set<String>) -> Bool {
        filtering ? !closedUnderFilter.contains(key) : contains(key, in: raw)
    }

    /// `closedUnderFilter` after the user opens or closes `key` under a filter.
    static func settingUnderFilter(_ key: String, open: Bool, in closed: Set<String>) -> Set<String> {
        var closed = closed
        if open { closed.remove(key) } else { closed.insert(key) }
        return closed
    }

    /// `raw` without the keys not in `live`, so rows that left the board
    /// don't pile up.
    static func pruned(_ raw: String, keeping live: Set<String>) -> String {
        keys(raw).filter(live.contains).joined(separator: "\n")
    }

    private static func keys(_ raw: String) -> [String] {
        raw.split(separator: "\n").map(String.init)
    }
}
