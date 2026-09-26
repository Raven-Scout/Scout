import Testing
import Foundation
@testable import Scout

@Suite("Schedule model")
struct ScheduleTests {

    @Test func calendarFireSemanticEqualityIgnoresId() {
        let a = CalendarFire(id: UUID(), weekday: 2, hour: 8, minute: 3)
        let b = CalendarFire(id: UUID(), weekday: 2, hour: 8, minute: 3)
        #expect(a.semanticallyEquals(b))
    }

    @Test func scheduleTriggerCalendarEqualityIgnoresFireIds() {
        let a: ScheduleTrigger = .calendar([
            CalendarFire(id: UUID(), weekday: 2, hour: 8, minute: 3)
        ])
        let b: ScheduleTrigger = .calendar([
            CalendarFire(id: UUID(), weekday: 2, hour: 8, minute: 3)
        ])
        #expect(a.semanticallyEquals(b))
    }

    @Test func scheduleTriggerIntervalEquality() {
        let a: ScheduleTrigger = .interval(seconds: 1800)
        let b: ScheduleTrigger = .interval(seconds: 1800)
        #expect(a.semanticallyEquals(b))
    }

    @Test func scheduleTriggerKindsDoNotMatch() {
        let a: ScheduleTrigger = .interval(seconds: 60)
        let b: ScheduleTrigger = .calendar([])
        #expect(!a.semanticallyEquals(b))
    }

    @Test func plistValueRoundTripsNested() {
        let v: PlistValue = .dict([
            "PATH": .string("/usr/bin"),
            "Nested": .array([.int(1), .bool(true)])
        ])
        let obj = v.toObject()
        let reparsed = PlistValue.from(object: obj)
        #expect(reparsed == v)
    }

    @Test func plistValueBoolDistinctFromInt() {
        // NSNumber round-trip can conflate Bool and Int; our parser must not.
        let b: PlistValue = .bool(true)
        let i: PlistValue = .int(1)
        #expect(b != i)
    }
}
