import Foundation

enum ScheduleTriggerFormatter {
    static func summary(for trigger: ScheduleTrigger) -> String {
        switch trigger {
        case .interval(let seconds):
            return intervalSummary(seconds: seconds)
        case .calendar(let fires):
            return calendarSummary(fires: fires)
        }
    }

    private static func intervalSummary(seconds: Int) -> String {
        if seconds >= 3600 && seconds % 3600 == 0 { return "Every \(seconds / 3600) hr" }
        if seconds >= 60 && seconds % 60 == 0 { return "Every \(seconds / 60) min" }
        return "Every \(seconds) sec"
    }

    private static func calendarSummary(fires: [CalendarFire]) -> String {
        guard !fires.isEmpty else { return "—" }

        // All fires nil-weekday → "Daily".
        if fires.allSatisfy({ $0.weekday == nil }) {
            let times = sortedTimeStrings(fires: fires).removingDuplicates()
            return "Daily " + times.joined(separator: ", ")
        }

        // Uniform cross-product: every weekday fired at every time?
        let uniqueWeekdays = Set(fires.compactMap { $0.weekday })
        let uniqueTimes = Set(fires.map { "\($0.hour):\(String(format: "%02d", $0.minute))" })
        let expected = uniqueWeekdays.count * uniqueTimes.count
        if fires.count == expected
            && !fires.contains(where: { $0.weekday == nil }) {
            let weekdayPart = weekdayGroupName(uniqueWeekdays)
            let times = sortedTimeStrings(fires: fires).removingDuplicates()
            return "\(weekdayPart) \(times.joined(separator: ", "))"
        }

        // Fallback: list each fire sorted by (weekday, hour, minute).
        let parts = fires
            .sorted { ($0.weekday ?? 0, $0.hour, $0.minute) < ($1.weekday ?? 0, $1.hour, $1.minute) }
            .map { fire -> String in
                let day = fire.weekday.map(shortWeekdayName) ?? "Daily"
                return "\(day) \(fire.hour):\(String(format: "%02d", fire.minute))"
            }
        return parts.joined(separator: ", ")
    }

    private static func sortedTimeStrings(fires: [CalendarFire]) -> [String] {
        fires.sorted { ($0.hour, $0.minute) < ($1.hour, $1.minute) }
            .map { "\($0.hour):\(String(format: "%02d", $0.minute))" }
    }

    private static func weekdayGroupName(_ set: Set<Int>) -> String {
        let weekdays: Set<Int> = [2, 3, 4, 5, 6]  // Mon-Fri (Calendar convention)
        let weekend: Set<Int> = [1, 7]            // Sun + Sat
        if set == weekdays { return "Weekdays" }
        if set == weekend { return "Sat–Sun" }
        return set.sorted().map(shortWeekdayName).joined(separator: "/")
    }

    private static func shortWeekdayName(_ calendarWeekday: Int) -> String {
        let names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        let idx = max(0, min(6, calendarWeekday - 1))
        return names[idx]
    }
}

private extension Array where Element: Hashable {
    func removingDuplicates() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}
