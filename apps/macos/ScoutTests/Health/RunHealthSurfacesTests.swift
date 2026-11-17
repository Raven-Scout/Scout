import Foundation
import SwiftUI
import Testing
@testable import Scout

/// Run health beyond the Health page (UI audit C8): the Control Center's red
/// banner and the `slot.*` warning lines on the Schedules page.
enum RunHealthSamples {
    static let now = try! Date("2026-11-17T12:00:00Z", strategy: .iso8601)

    static func finding(_ id: String, _ severity: String = "yellow", since: String? = nil,
                        summary: String = "engine words") -> RunHealth.Finding {
        RunHealth.Finding(id: id, severity: severity, since: since, summary: summary,
                          fix: "Open Settings ▸ Engine.", source: "schedule")
    }

    static func health(_ severity: DoctorReport.Severity, _ findings: [RunHealth.Finding]) -> RunHealth {
        RunHealth(severity: severity, evaluatedAt: "2026-11-17T12:00:00Z", lastOkAt: nil,
                  findings: findings, slots: [])
    }

    static let stale = finding("slot.stale:morning-briefing", "red", since: "2026-11-14T12:00:00Z",
                               summary: "morning-briefing last fired 3 days ago.")
    static let silent = finding("slot.fired_silent:morning-briefing", since: "2026-11-17T07:00:00Z")
    static let never = finding("slot.never_fired:my-own-slot")
    static let gates = finding("gates.probe_unsafe", "red", summary: "Runs were skipped: the privacy check couldn't run.")
    static let note = finding("slot.stale:evening-consolidation", "note")
}

@Suite("Run health — slot findings")
struct SlotFindingTests {
    typealias S = RunHealthSamples

    @Test(arguments: [
        ("slot.stale:morning-briefing", "stale", "morning-briefing"),
        ("slot.fired_silent:a:b", "fired_silent", "a:b"),
    ])
    func parsesSlotIds(id: String, kind: String, key: String) throws {
        let parsed = try #require(RunOutcomeDisplay.slotFinding(id))
        #expect(parsed.kind == kind && parsed.key == key)
    }

    @Test(arguments: ["gates.probe_unsafe", "slot.stale", "slot.:key", "slot.stale:", "runs.slot.stale:x"])
    func ignoresOtherIds(id: String) {
        #expect(RunOutcomeDisplay.slotFinding(id) == nil)
    }

    @Test func linesSpeakPlainly() {
        #expect(RunOutcomeDisplay.slotLine(S.stale, now: S.now) == "Hasn't run for 3 days")
        #expect(RunOutcomeDisplay.slotLine(S.silent, now: S.now)
                == "Started 5 hours ago, but Scout has no record of how the run ended")
        #expect(RunOutcomeDisplay.slotLine(S.never, now: S.now) == "Hasn't run yet")
        #expect(RunOutcomeDisplay.slotLine(S.finding("slot.stale:x"), now: S.now) == "Hasn't run lately")
        // A kind this build doesn't know keeps the engine's sentence, with the schedule's name.
        let future = S.finding("slot.too_slow:morning-briefing", summary: "morning-briefing ran long.")
        #expect(RunOutcomeDisplay.slotLine(future, now: S.now) == "Morning briefing ran long.")
    }

    static let ageCases: [(Double, String)] = [
        (30, "1 min"), (2400, "40 min"), (3600, "1 hour"), (18000, "5 hours"),
        (169200, "47 hours"), (259200, "3 days"), (-5, "1 min"),
    ]

    @Test(arguments: ageCases)
    func ages(seconds: Double, expected: String) {
        #expect(RunOutcomeDisplay.age(seconds) == expected)
    }

    @Test func warningsGroupBySlotWorstFirstAndSkipNotes() {
        let health = S.health(.red, [S.gates, S.silent, S.stale, S.never, S.note])
        let warnings = RunOutcomeDisplay.slotWarnings(health, now: S.now)
        #expect(Set(warnings.keys) == ["morning-briefing", "my-own-slot"])
        #expect(warnings["morning-briefing"]?.map(\.severity) == [.error, .warning])
        #expect(warnings["morning-briefing"]?.first?.text == "Hasn't run for 3 days")
        #expect(warnings["my-own-slot"]?.map(\.text) == ["Hasn't run yet"])
        #expect(RunOutcomeDisplay.slotWarnings(nil).isEmpty)
    }

    /// The shared fixture has no `slot.*` findings: no schedule gets a line.
    @Test func fixtureHasNoSlotLines() throws {
        let ledger = try RunLedgerContractTests.fixture()
        #expect(RunOutcomeDisplay.slotWarnings(ledger.health).isEmpty)
    }
}

@Suite("Run health — Control Center banner")
struct RunHealthBannerModelTests {
    typealias S = RunHealthSamples

    @Test func onlyRedHealthHasABanner() {
        #expect(RunOutcomeDisplay.bannerProblem(nil) == nil)
        #expect(RunOutcomeDisplay.bannerProblem(S.health(.yellow, [S.silent])) == nil)
        #expect(RunOutcomeDisplay.bannerProblem(S.health(.green, [])) == nil)
    }

    @Test func leadsWithTheFirstRedFinding() throws {
        let p = try #require(RunOutcomeDisplay.bannerProblem(S.health(.red, [S.silent, S.gates, S.note]), now: S.now))
        #expect(p.severity == .error)
        #expect(p.title == S.gates.summary)
        #expect(p.fix == S.gates.fix)
        // The note isn't counted; the yellow finding is.
        #expect(p.impact == "1 more problem is on the Health page.")
        #expect(p.details?.contains("Finding: gates.probe_unsafe") == true)
    }

    @Test func aSlotFindingNamesItsSchedule() throws {
        let p = try #require(RunOutcomeDisplay.bannerProblem(S.health(.red, [S.stale, S.silent, S.never]), now: S.now))
        #expect(p.title == "Morning briefing: Hasn't run for 3 days")
        #expect(p.impact == "2 more problems are on the Health page.")
    }

    @Test func redWithoutFindingsStillSaysSo() throws {
        let p = try #require(RunOutcomeDisplay.bannerProblem(S.health(.red, [S.note])))
        #expect(p.title == "Scheduled runs need fixing")
        #expect(p.impact == nil)
    }

    @Test func theSharedFixtureIsRed() throws {
        let ledger = try RunLedgerContractTests.fixture()
        let p = try #require(RunOutcomeDisplay.bannerProblem(ledger.health))
        #expect(p.details?.contains("Finding: gates.probe_unsafe") == true)
        #expect(p.impact == "1 more problem is on the Health page.")
    }
}

/// Renders the banner and the Schedules rows with warnings.
@MainActor
@Suite("View smoke — run health surfaces", .serialized)
struct RunHealthSurfacesSmokeTests {
    typealias S = RunHealthSamples

    @Test("the banner renders when red and is empty otherwise")
    func bannerRenders() throws {
        let red = try RunLedgerContractTests.fixture().health
        for health in [red, S.health(.red, [S.stale, S.silent]), S.health(.yellow, [S.silent]), nil] {
            ViewHost.render(RunHealthBanner(health: health, openHealth: {}, now: S.now),
                            size: CGSize(width: 900, height: 200))
        }
    }

    @Test("schedule rows and cards render their warning lines")
    func schedulesRender() {
        var selected: String? = "morning-briefing"
        let binding = Binding(get: { selected }, set: { selected = $0 })
        let warnings = RunOutcomeDisplay.slotWarnings(S.health(.red, [S.stale, S.silent]), now: S.now)
        let all = warnings["morning-briefing"] ?? []
        #expect(all.count == 2)
        ViewHost.render(SlotTableRow(slot: SmokeFixtures.slot(), isSelected: false, nameWidth: 220, warnings: all),
                        size: CGSize(width: 900, height: 80))
        ViewHost.render(SlotCard(slot: SmokeFixtures.slot(), isSelected: true, warnings: all),
                        size: CGSize(width: 300, height: 260))
        let keyed = Dictionary(SmokeFixtures.slots.map { ($0.key, all) }, uniquingKeysWith: { a, _ in a })
        ViewHost.render(SchedulesMasterTable(slots: SmokeFixtures.slots, newDraftSlot: nil,
                                             selectedSlotKey: binding, warnings: keyed),
                        size: CGSize(width: 900, height: 500))
        ViewHost.render(SchedulesMasterCards(slots: SmokeFixtures.slots, newDraftSlot: nil,
                                             selectedSlotKey: binding, warnings: keyed),
                        size: CGSize(width: 900, height: 600))
    }
}
