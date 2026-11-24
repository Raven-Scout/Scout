import SwiftUI
import Testing
@testable import Scout

@MainActor
@Suite("Engagement trend", .serialized)
struct TrendModelTests {

    /// Mondays start the week; everything in UTC so the days are exact.
    private static let calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        calendar.firstWeekday = 2
        return calendar
    }()

    private static func date(_ day: String, hour: Int = 12) -> Date {
        let parts = day.split(separator: "-").compactMap { Int($0) }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: hour))!
    }

    private static func day(_ date: String, feedback: Int = 0, reliance: Int = 0, noise: Int = 0) throws -> EngagementDay {
        let json = #"{"date":"\#(date)","feedback":\#(feedback),"reliance":\#(reliance),"noise":\#(noise)}"#
        return try JSONDecoder().decode(EngagementDay.self, from: Data(json.utf8))
    }

    private static func change(_ id: String, at ts: String, verdict: String? = nil, state: String = "done",
                               inferred: Bool = false, summary: String = "Say so when a search finds nothing") throws -> ChangeRow {
        let verdictJSON = verdict.map { #""\#($0)""# } ?? "null"
        let json = """
        {"id":"\(id)","kind":"phase","summary":"\(summary)","commit":"abc1234","ts":"\(ts)",
         "why":{"type":"unknown","ref":"","gloss":"","inferred":\(inferred)},
         "test":{"state":"\(state)","verdict":\(verdictJSON)}}
        """
        return try JSONDecoder().decode(ChangeRow.self, from: Data(json.utf8))
    }

    // MARK: - Weeks

    @Test("days are bucketed by week and summed")
    func weeksBucketAndSumByWeek() throws {
        let days = [
            try Self.day("2026-11-02", feedback: 1),
            try Self.day("2026-11-04", reliance: 2),
            try Self.day("2026-11-08", noise: 1),      // the Sunday that closes the first week
            try Self.day("2026-11-09", feedback: 3),   // a Monday starts the next one
            try Self.day("not-a-day", feedback: 99),   // skipped, never a crash
            try Self.day("2026-13-45", feedback: 99),
        ]
        let weeks = TrendModel.weeks(days, range: .month, today: Self.date("2026-11-12"), calendar: Self.calendar)
        #expect(weeks == [
            WeekBucket(start: Self.date("2026-11-02", hour: 0), feedback: 1, reliance: 2, noise: 1),
            WeekBucket(start: Self.date("2026-11-09", hour: 0), feedback: 3, reliance: 0, noise: 0),
        ])
    }

    @Test("the range limits the weeks: a month shows at most 5, a year at most 53")
    func rangeLimitsTheWeeks() throws {
        let today = Self.date("2026-11-12")
        let start = Self.date("2025-09-01")
        let days = try (0..<440).map { offset -> EngagementDay in
            let date = Self.calendar.date(byAdding: .day, value: offset, to: start)!
            let parts = Self.calendar.dateComponents([.year, .month, .day], from: date)
            return try Self.day(String(format: "%04d-%02d-%02d", parts.year!, parts.month!, parts.day!), feedback: 1)
        }
        let month = TrendModel.weeks(days, range: .month, today: today, calendar: Self.calendar)
        let year = TrendModel.weeks(days, range: .year, today: today, calendar: Self.calendar)
        #expect(!month.isEmpty && month.count <= 5)
        #expect(!year.isEmpty && year.count <= 53)
        #expect(year.count > month.count)
        #expect(month.last?.start == Self.date("2026-11-09", hour: 0))
        // Nothing after today.
        #expect(month.map(\.feedback).reduce(0, +) <= 5 * 7)
    }

    // MARK: - Markers

    @Test("a change outside the range gets no marker")
    func markersSkipChangesOutsideTheRange() throws {
        let inside = try Self.change("chg-000000000001", at: "2026-11-10T12:00:00+00:00")
        let tooOld = try Self.change("chg-000000000002", at: "2026-08-01T12:00:00+00:00")
        let future = try Self.change("chg-000000000003", at: "2026-11-20T12:00:00+00:00")
        let today = Self.date("2026-11-12")
        #expect(TrendModel.markers([tooOld, inside, future], range: .month, today: today, calendar: Self.calendar).map(\.id)
                == ["chg-000000000001"])
        #expect(TrendModel.markers([future, inside, tooOld], range: .halfYear, today: today, calendar: Self.calendar).map(\.id)
                == ["chg-000000000002", "chg-000000000001"])
    }

    @Test("the tone follows the verdict")
    func toneFollowsTheVerdict() {
        func tone(_ verdict: String?, state: String = "done") -> VerdictTone {
            TrendModel.tone(ChangeTest(state: state, verdict: verdict))
        }
        #expect(tone("helped") == .helped)
        #expect(tone("hurt") == .bad)
        #expect(tone("rolled back") == .bad)
        #expect(tone("no clear effect") == .neutral)
        #expect(tone("can't tell") == .neutral)
        #expect(tone(nil, state: "testing") == .testing)
        #expect(tone(nil, state: "not-tested") == .neutral)
        #expect(tone("helped", state: "testing") == .helped)
    }

    @Test("a marker is hollow when the reason was inferred")
    func hollowWhenTheWhyWasInferred() throws {
        let inferred = try Self.change("chg-000000000001", at: "2026-11-10T12:00:00+00:00", inferred: true)
        let recorded = try Self.change("chg-000000000002", at: "2026-11-10T12:00:00+00:00", inferred: false)
        let markers = TrendModel.markers([inferred, recorded], range: .month, today: Self.date("2026-11-12"),
                                         calendar: Self.calendar)
        #expect(markers.map(\.hollow) == [true, false])
    }

    // MARK: - View
    // The tests that read the accessibility tree live in ChangesViewTests: that suite is serialized,
    // and the tree reader flips an app-wide switch that two suites must not toggle at once.

    @Test("no days and no changes: no weeks and no markers")
    func emptyDocumentsRenderEmptyStates() {
        #expect(TrendModel.weeks([], range: .quarter, today: Self.date("2026-11-12"), calendar: Self.calendar) == [])
        #expect(TrendModel.markers([], range: .quarter, today: Self.date("2026-11-12"), calendar: Self.calendar) == [])
    }

    @Test("the marker label names the change and where its test stands")
    func markerLabelWords() throws {
        let testing = try Self.change("chg-00000000000b", at: "2026-11-10T12:00:00+00:00", state: "testing")
        let hurt = try Self.change("chg-00000000000c", at: "2026-11-10T12:00:00+00:00", verdict: "hurt")
        let fresh = try Self.change("chg-00000000000d", at: "2026-11-10T12:00:00+00:00", state: "not-tested")
        #expect(TrendModel.markerLabel(testing).hasSuffix("still being tested"))
        #expect(TrendModel.markerLabel(hurt).hasSuffix("hurt"))
        #expect(TrendModel.markerLabel(fresh).hasSuffix("not tested yet"))
    }

    @Test("the Changes tab renders with the trend in it")
    func changesViewShowsTheTrend() async throws {
        let service = try await LoadedChangesService.make()
        ViewHost.render(ChangesView().environmentObject(service).environmentObject(LoadedChangesService.decisions()))
    }
}
