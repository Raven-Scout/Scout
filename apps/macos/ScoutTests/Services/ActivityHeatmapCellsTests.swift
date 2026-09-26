import Testing
import Foundation
@testable import Scout

@Suite("ActivityHeatmapView cell building")
struct ActivityHeatmapCellsTests {
    private let cal = Calendar.current

    private func daysAgo(_ n: Int) -> Date {
        cal.date(byAdding: .day, value: -n, to: cal.startOfDay(for: Date()))!
    }

    @Test func bucketsRunsToCorrectDaysWithCounts() {
        let today = cal.startOfDay(for: Date())
        let runs = [
            Run.make(startedAt: today.addingTimeInterval(3600),  status: .success, cost: 2),
            Run.make(startedAt: today.addingTimeInterval(7200),  status: .failure, cost: 1),
            Run.make(startedAt: daysAgo(2).addingTimeInterval(3600), status: .success, cost: 5),
        ]
        let cells = ActivityHeatmapView.heatmapCells(runs: runs, dayCount: 7, calendar: cal, today: today)
        #expect(cells.count == 7)

        let todayCell = cells.first { cal.isDate($0.date, inSameDayAs: today) }
        #expect(todayCell?.successes == 1)
        #expect(todayCell?.failures == 1)
        #expect(todayCell?.cost == 3)

        let twoAgo = cells.first { cal.isDate($0.date, inSameDayAs: self.daysAgo(2)) }
        #expect(twoAgo?.successes == 1)
        #expect(twoAgo?.failures == 0)
        #expect(twoAgo?.cost == 5)
    }

    @Test func emptyRunsProduceZeroedCells() {
        let cells = ActivityHeatmapView.heatmapCells(
            runs: [], dayCount: 31, calendar: cal, today: cal.startOfDay(for: Date())
        )
        #expect(cells.count == 31)
        #expect(cells.allSatisfy { $0.successes == 0 && $0.failures == 0 && $0.cost == 0 })
    }

    @Test func failureStatusesAllCountAsFailures() {
        let today = cal.startOfDay(for: Date())
        let runs = [
            Run.make(startedAt: today.addingTimeInterval(60),  status: .timeout),
            Run.make(startedAt: today.addingTimeInterval(120), status: .rateLimited),
            Run.make(startedAt: today.addingTimeInterval(180), status: .failure),
            Run.make(startedAt: today.addingTimeInterval(240), status: .success),
        ]
        let cells = ActivityHeatmapView.heatmapCells(runs: runs, dayCount: 1, calendar: cal, today: today)
        #expect(cells.count == 1)
        #expect(cells[0].successes == 1)
        #expect(cells[0].failures == 3)   // timeout + rateLimited + failure
    }

    @Test func runsOutsideWindowAreExcluded() {
        let today = cal.startOfDay(for: Date())
        let runs = [Run.make(startedAt: daysAgo(100), status: .success)]
        let cells = ActivityHeatmapView.heatmapCells(runs: runs, dayCount: 7, calendar: cal, today: today)
        #expect(cells.allSatisfy { $0.successes == 0 && $0.failures == 0 })
    }

    /// The bucketed implementation must match the original per-day
    /// `Calendar.isDate(inSameDayAs:)` semantics it replaced.
    @Test func matchesReferenceIsDateLogic() {
        let today = cal.startOfDay(for: Date())
        let failStatuses: [RunStatus] = [.failure, .timeout, .rateLimited]
        var runs: [Run] = []
        for i in 0..<40 {
            let status: RunStatus = (i % 3 == 0) ? .failure : .success
            let started = daysAgo(i % 10).addingTimeInterval(Double(i) * 600)
            runs.append(Run.make(startedAt: started, status: status, cost: Decimal(i)))
        }
        let dayCount = 14
        let start = cal.date(byAdding: .day, value: -(dayCount - 1), to: today)!

        var refSucc: [Int] = []
        var refFail: [Int] = []
        for offset in 0..<dayCount {
            let day = cal.date(byAdding: .day, value: offset, to: start)!
            let dayRuns = runs.filter { cal.isDate($0.startedAt, inSameDayAs: day) }
            refSucc.append(dayRuns.filter { $0.status == .success }.count)
            refFail.append(dayRuns.filter { failStatuses.contains($0.status) }.count)
        }

        let cells = ActivityHeatmapView.heatmapCells(runs: runs, dayCount: dayCount, calendar: cal, today: today)
        #expect(cells.map(\.successes) == refSucc)
        #expect(cells.map(\.failures) == refFail)
    }
}
