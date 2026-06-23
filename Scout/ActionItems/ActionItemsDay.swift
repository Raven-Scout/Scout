import Foundation

/// Single source of truth for the action-items **daily file** date contract.
///
/// The scout-plugin engine names daily files `action-items-YYYY-MM-DD.md`
/// using Python's `date.today()` — i.e. the **system-local** calendar day
/// (`engine/scout/paths.py`). The app must use the same timezone so it reads
/// and writes the same file the engine does. Previously every call site
/// hardcoded `America/New_York`, which resolved to the wrong daily file for
/// any user outside Eastern time (issue #46).
///
/// `timeZone` defaults to `.current` (the engine's behavior); it's injectable
/// so the conversion can be tested deterministically against a fixed zone.
enum ActionItemsDay {
    /// `YYYY-MM-DD` stem naming the daily file for `date`'s calendar day.
    static func stem(for date: Date, timeZone: TimeZone = .current) -> String {
        formatter(timeZone).string(from: date)
    }

    /// Parse a `YYYY-MM-DD` stem back to the start-of-day instant in `timeZone`,
    /// or `nil` if it isn't a valid date.
    static func date(fromStem stem: String, timeZone: TimeZone = .current) -> Date? {
        formatter(timeZone).date(from: stem)
    }

    /// Start of the current calendar day in `timeZone` — the default
    /// "today" the daily view opens to.
    static func today(timeZone: TimeZone = .current) -> Date {
        var cal = Calendar(identifier: .iso8601)
        cal.timeZone = timeZone
        return cal.startOfDay(for: Date())
    }

    private static func formatter(_ timeZone: TimeZone) -> DateFormatter {
        let fmt = DateFormatter()
        fmt.calendar = Calendar(identifier: .iso8601)
        fmt.locale = Locale(identifier: "en_US_POSIX")
        fmt.dateFormat = "yyyy-MM-dd"
        fmt.timeZone = timeZone
        return fmt
    }
}
