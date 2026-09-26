import Testing
import Foundation
@testable import Scout

@Suite("ScheduleTriggerFormatter.summary")
struct ScheduleTriggerFormatterTests {

    @Test func intervalInMinutes() {
        #expect(ScheduleTriggerFormatter.summary(for: .interval(seconds: 1800))
                == "Every 30 min")
    }

    @Test func intervalBelowMinute() {
        #expect(ScheduleTriggerFormatter.summary(for: .interval(seconds: 45))
                == "Every 45 sec")
    }

    @Test func intervalInHours() {
        #expect(ScheduleTriggerFormatter.summary(for: .interval(seconds: 7200))
                == "Every 2 hr")
    }

    @Test func calendarSingleDaily() {
        let t: ScheduleTrigger = .calendar([CalendarFire(weekday: nil, hour: 22, minute: 15)])
        #expect(ScheduleTriggerFormatter.summary(for: t) == "Daily 22:15")
    }

    @Test func calendarWeekdaysSingleTime() {
        let t: ScheduleTrigger = .calendar(
            (2...6).map { CalendarFire(weekday: $0, hour: 8, minute: 3) }
        )
        #expect(ScheduleTriggerFormatter.summary(for: t) == "Weekdays 8:03")
    }

    @Test func calendarWeekdaysMultipleTimes() {
        var fires: [CalendarFire] = []
        for d in 2...6 {
            fires.append(CalendarFire(weekday: d, hour: 8, minute: 3))
            fires.append(CalendarFire(weekday: d, hour: 11, minute: 3))
        }
        #expect(ScheduleTriggerFormatter.summary(for: .calendar(fires))
                == "Weekdays 8:03, 11:03")
    }

    @Test func calendarWeekendSingleTime() {
        let t: ScheduleTrigger = .calendar([
            CalendarFire(weekday: 1, hour: 8, minute: 0),
            CalendarFire(weekday: 7, hour: 8, minute: 0)
        ])
        #expect(ScheduleTriggerFormatter.summary(for: t) == "Sat–Sun 8:00")
    }

    @Test func calendarMixedFallback() {
        let t: ScheduleTrigger = .calendar([
            CalendarFire(weekday: 2, hour: 8, minute: 0),
            CalendarFire(weekday: 5, hour: 12, minute: 30)
        ])
        #expect(ScheduleTriggerFormatter.summary(for: t) == "Mon 8:00, Thu 12:30")
    }

    @Test func calendarEmptyIsIdle() {
        #expect(ScheduleTriggerFormatter.summary(for: .calendar([])) == "—")
    }
}
