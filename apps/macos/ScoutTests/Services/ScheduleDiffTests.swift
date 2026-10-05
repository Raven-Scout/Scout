import Testing
import Foundation
@testable import Scout

@Suite("ScheduleDiff.summarize")
struct ScheduleDiffTests {

    private func base() -> Schedule {
        Schedule(
            id: "com.scout.x", label: "com.scout.x",
            runnerScript: URL(fileURLWithPath: "/s.sh"),
            environment: ["A": "1"],
            trigger: .calendar([CalendarFire(weekday: 2, hour: 8, minute: 0)])
        )
    }

    @Test func identicalYieldsEmpty() {
        #expect(ScheduleDiff.summarize(original: base(), edited: base()) == "")
    }

    @Test func runnerOnly() {
        var e = base()
        e.runnerScript = URL(fileURLWithPath: "/other.sh")
        #expect(ScheduleDiff.summarize(original: base(), edited: e) == "runner")
    }

    @Test func triggerOnly() {
        var e = base()
        e.trigger = .calendar([CalendarFire(weekday: 2, hour: 9, minute: 0)])
        #expect(ScheduleDiff.summarize(original: base(), edited: e) == "trigger")
    }

    @Test func envOnly() {
        var e = base()
        e.environment = ["A": "2"]
        #expect(ScheduleDiff.summarize(original: base(), edited: e) == "env")
    }

    @Test func multipleFieldsCommaJoined() {
        var e = base()
        e.runnerScript = URL(fileURLWithPath: "/other.sh")
        e.environment = ["A": "2"]
        #expect(ScheduleDiff.summarize(original: base(), edited: e) == "runner, env")
    }
}
