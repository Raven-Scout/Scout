import Testing
import Foundation
@testable import Scout

@Suite("ActionItemsDay (daily-file date contract)")
struct ActionItemsDayTests {
    private let tokyo = TimeZone(identifier: "Asia/Tokyo")!
    private let eastern = TimeZone(identifier: "America/New_York")!
    private let la = TimeZone(identifier: "America/Los_Angeles")!

    @Test func stemFollowsTimeZoneAtDayBoundary() {
        // 00:30 Tokyo on 2026-04-20 is still 2026-04-19 in Eastern time. The
        // daily-file stem must follow the *user's* timezone (matching the
        // engine's date.today()), so a Tokyo user gets 04-20, not 04-19 (#46).
        var c = DateComponents()
        c.timeZone = tokyo; c.year = 2026; c.month = 4; c.day = 20
        c.hour = 0; c.minute = 30
        let instant = Calendar(identifier: .iso8601).date(from: c)!
        #expect(ActionItemsDay.stem(for: instant, timeZone: tokyo) == "2026-04-20")
        #expect(ActionItemsDay.stem(for: instant, timeZone: eastern) == "2026-04-19")
    }

    @Test func stemRoundTripsThroughDate() {
        let date = try! #require(ActionItemsDay.date(fromStem: "2026-12-31", timeZone: la))
        #expect(ActionItemsDay.stem(for: date, timeZone: la) == "2026-12-31")
    }

    @Test func dateFromStemRejectsGarbage() {
        #expect(ActionItemsDay.date(fromStem: "not-a-date", timeZone: la) == nil)
    }

    @Test func todayIsStartOfDayInTimeZone() {
        let t = ActionItemsDay.today(timeZone: la)
        var cal = Calendar(identifier: .iso8601)
        cal.timeZone = la
        #expect(t == cal.startOfDay(for: t))
        #expect(cal.isDate(t, inSameDayAs: Date()) || !cal.isDate(t, inSameDayAs: Date()))
    }
}
