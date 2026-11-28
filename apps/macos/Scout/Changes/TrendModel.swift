import Foundation

/// How a change's test reads on the trend: the colour family its marker is drawn in.
nonisolated enum VerdictTone: Equatable, Sendable {
    /// `helped`.
    case helped
    /// `no clear effect`, `can't tell`, or not tested yet.
    case neutral
    /// Being tested, with no verdict yet.
    case testing
    /// `hurt` or `rolled back`.
    case bad
}

/// One week of the owner's engagement: counts summed over the days that fall in it.
nonisolated struct WeekBucket: Equatable, Sendable, Identifiable {
    /// The first day of the week (the calendar's first weekday), at the start of that day.
    let start: Date
    let feedback: Int
    let reliance: Int
    let noise: Int

    var id: Date { start }
}

/// A change placed on the time axis.
nonisolated struct ChangeMarker: Equatable, Sendable, Identifiable {
    let id: String
    let date: Date
    let tone: VerdictTone
    /// True when the reason for the change was inferred rather than recorded: the marker is drawn hollow.
    let hollow: Bool
}

/// The numbers behind the engagement trend. Pure: today and the calendar come in as values.
nonisolated enum TrendModel {

    /// Weekly buckets inside the range, oldest first. A week with no recorded day is left out; a day
    /// whose date doesn't parse is skipped.
    static func weeks(_ days: [EngagementDay], range: HeatmapRange, today: Date, calendar: Calendar) -> [WeekBucket] {
        let window = window(range: range, today: today, calendar: calendar)
        let formatter = dayFormatter(calendar)
        var sums: [Date: (feedback: Int, reliance: Int, noise: Int)] = [:]
        for day in days {
            guard let date = formatter.date(from: day.date), window.contains(date) else { continue }
            let key = weekStart(of: date, calendar: calendar)
            var sum = sums[key] ?? (0, 0, 0)
            sum.feedback += day.feedback
            sum.reliance += day.reliance
            sum.noise += day.noise
            sums[key] = sum
        }
        return sums.keys.sorted().map { key in
            let sum = sums[key] ?? (0, 0, 0)
            return WeekBucket(start: key, feedback: sum.feedback, reliance: sum.reliance, noise: sum.noise)
        }
    }

    /// The changes that landed inside the range, oldest first.
    static func markers(_ changes: [ChangeRow], range: HeatmapRange, today: Date, calendar: Calendar) -> [ChangeMarker] {
        let window = window(range: range, today: today, calendar: calendar)
        return changes
            .filter { window.contains($0.ts) }
            .sorted { $0.ts < $1.ts }
            .map { ChangeMarker(id: $0.id, date: $0.ts, tone: tone($0.test), hollow: $0.why.inferred) }
    }

    static func tone(_ test: ChangeTest) -> VerdictTone {
        switch test.verdict {
        case "helped": return .helped
        case "hurt", "rolled back": return .bad
        case nil where test.state == "testing": return .testing
        default: return .neutral
        }
    }

    /// The verdict in plain words, for a marker's spoken label.
    static func verdictWords(_ test: ChangeTest) -> String {
        if let verdict = test.verdict, !verdict.isEmpty { return verdict }
        return test.state == "testing" ? "still being tested" : "not tested yet"
    }

    /// What a marker says out loud: the change's own summary and where its test stands. Never an id.
    static func markerLabel(_ change: ChangeRow) -> String {
        "\(change.summary), \(verdictWords(change.test))"
    }

    // MARK: - Window

    /// The number of weeks each range shows (the range's days, rounded up to whole weeks).
    static func weekCount(_ range: HeatmapRange) -> Int {
        switch range {
        case .month: return 5
        case .quarter: return 13
        case .halfYear: return 26
        case .year: return 53
        }
    }

    /// From the first day of the oldest week shown up to the end of today.
    static func window(range: HeatmapRange, today: Date, calendar: Calendar) -> Range<Date> {
        let thisWeek = weekStart(of: today, calendar: calendar)
        let start = calendar.date(byAdding: .weekOfYear, value: -(weekCount(range) - 1), to: thisWeek) ?? thisWeek
        let end = calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: today)) ?? today
        return start..<max(start, end)
    }

    static func weekStart(of date: Date, calendar: Calendar) -> Date {
        calendar.dateInterval(of: .weekOfYear, for: date)?.start ?? calendar.startOfDay(for: date)
    }

    /// `yyyy-MM-dd`, read in the calendar's own time zone and never leniently.
    private static func dayFormatter(_ calendar: Calendar) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.isLenient = false
        return formatter
    }
}
