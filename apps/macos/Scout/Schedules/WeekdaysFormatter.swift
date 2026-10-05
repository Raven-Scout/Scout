import Foundation

/// Pure helper that turns a slot's `weekdays` array into a human-readable label
/// shown beneath the day-circle strip in the Schedules table view.
///
/// Resolution order (first match wins):
///   - Empty → "" (caller handles)
///   - All 7 → "every day"
///   - Mon–Fri → "weekdays"
///   - Sat+Sun → "weekends"
///   - Single day → day name
///   - Contiguous block → "Mon-Wed" style
///   - Otherwise → comma-list, in canonical Mon→Sun order
enum WeekdaysFormatter {

    private static let canonical = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    private static let weekdays = Set(["Mon", "Tue", "Wed", "Thu", "Fri"])
    private static let weekends = Set(["Sat", "Sun"])

    static func label(for days: [String]) -> String {
        let set = Set(days)
        guard !set.isEmpty else { return "" }
        if set.count == 7                       { return "every day" }
        if set == weekdays                      { return "weekdays" }
        if set == weekends                      { return "weekends" }

        // Re-sort into canonical order before deciding contiguous-vs-list.
        let sorted = canonical.filter { set.contains($0) }

        if sorted.count == 1                    { return sorted[0] }
        if isContiguous(sorted)                 { return "\(sorted.first!)-\(sorted.last!)" }
        return sorted.joined(separator: ", ")
    }

    /// True when the input is a contiguous slice of the canonical Mon→Sun order.
    private static func isContiguous(_ sorted: [String]) -> Bool {
        guard let first = sorted.first, let firstIdx = canonical.firstIndex(of: first) else {
            return false
        }
        for (offset, day) in sorted.enumerated() {
            let idx = firstIdx + offset
            guard idx < canonical.count, canonical[idx] == day else { return false }
        }
        return true
    }
}
