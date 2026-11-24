import SwiftUI
import Testing
@testable import Scout

@MainActor
@Suite("Changes tab", .serialized)
struct ChangesViewTests {

    @Test("the sidebar badge counts what awaits your OK plus suspect changes not yet undone")
    func badgeCountsFromTheLoadedSnapshot() async throws {
        let service = try await LoadedChangesService.make()
        // Fixture: one governance item awaiting OK (the other is approved) and one suspect change.
        #expect(ChangesView.needsYouCount(service.state) == 2)
    }

    @Test("no badge unless the snapshot is loaded")
    func noBadgeWhenNotLoaded() {
        for state: ChangesService.State in [.idle, .loading, .unsupported, .failed("x")] {
            #expect(ChangesView.needsYouCount(state) == 0)
        }
    }

    @Test("a suspect change that was already undone does not count")
    func undoneSuspectDoesNotCount() async throws {
        let service = try await LoadedChangesService.make()
        let snapshot = try #require(service.snapshot)
        let suspect = try #require(snapshot.changes.changes.first { $0.test.suspect })
        #expect(ChangesView.isSuspectAndNotUndone(suspect.test))
        #expect(!ChangesView.isSuspectAndNotUndone(ChangeTest(suspect: true, rolledBackBy: "chg-000000000009")))
        #expect(!ChangesView.isSuspectAndNotUndone(ChangeTest(suspect: false)))
    }

    @Test("the rendered digest card shows plain words, never a bare change id")
    func digestHasNoBareIds() async throws {
        let service = try await LoadedChangesService.make()
        let digest = try #require(service.snapshot).digest
        // Read what the card actually draws, from the accessibility tree.
        let nodes = await AccessibilityTree.nodes(of: DigestCard(digest: digest).frame(width: 700),
                                                  size: CGSize(width: 700, height: 900))
        let shown = nodes.flatMap { [$0.label, $0.value] }.filter { !$0.isEmpty }
        let all = shown.joined(separator: "\n")
        for text in shown { #expect(!text.contains("chg-"), "bare id in: \(text)") }
        #expect(all.contains("A phase change"), "testing row missing:\n\(all)")
        #expect(all.contains("A rollback change"), "shipped row missing:\n\(all)")
        #expect(all.contains("A wishlist-done change"), "learned row missing:\n\(all)")
        #expect(all.contains("Say so when a Gmail search finds nothing"), "up-next row missing:\n\(all)")
        #expect(all.contains("Needs your OK (1)"), "needs-OK line missing:\n\(all)")
    }

    private static func trendView(days: [EngagementDay], changes: [ChangeRow]) -> some View {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let today = calendar.date(from: DateComponents(year: 2026, month: 11, day: 12, hour: 12))!
        return EngagementTrendView(days: days, changes: changes, today: today, calendar: calendar, onSelect: { _ in })
            .frame(width: 700)
    }

    @Test("with no engagement the trend says so")
    func emptyTrendSaysSo() async {
        let nodes = await AccessibilityTree.nodes(of: Self.trendView(days: [], changes: []), size: CGSize(width: 700, height: 400))
        let shown = nodes.flatMap { [$0.label, $0.value] }
        #expect(shown.contains("No engagement recorded yet."), "empty state missing:\n\(nodes)")
    }

    @Test("each trend marker is announced in plain words, never as a bare change id")
    func trendMarkersSpeakPlainly() async throws {
        let day = try JSONDecoder().decode(
            EngagementDay.self, from: Data(#"{"date":"2026-11-10","feedback":2,"reliance":1,"noise":1}"#.utf8))
        let change = try JSONDecoder().decode(ChangeRow.self, from: Data("""
        {"id":"chg-00000000000a","kind":"phase","summary":"Say so when a search finds nothing","commit":"abc1234",
         "ts":"2026-11-10T12:00:00+00:00","why":{"type":"unknown","ref":"","gloss":"","inferred":true},
         "test":{"state":"done","verdict":"helped"}}
        """.utf8))
        let nodes = await AccessibilityTree.nodes(of: Self.trendView(days: [day], changes: [change]),
                                                  size: CGSize(width: 700, height: 400))
        let shown = nodes.flatMap { [$0.label, $0.value] }.filter { !$0.isEmpty }
        for text in shown { #expect(!text.contains("chg-"), "bare id in: \(text)") }
        #expect(shown.contains("Say so when a search finds nothing, helped"), "marker label missing:\n\(nodes)")
    }

    @Test("engagement reads with its comparison, or says there is none")
    func engagementSentence() {
        #expect(DigestCard.engagementLine(DigestEngagement(feedback: 5, reliance: 3, thisWeek: 8, lastWeek: 0, changePct: nil),
                                          noise: DigestNoise(thisWeek: 2, lastWeek: 0))
                == "Your engagement: 8 this week (nothing from last week to compare), noise 2 (up 2)")
        #expect(DigestCard.engagementLine(DigestEngagement(feedback: 5, reliance: 3, thisWeek: 9, lastWeek: 8, changePct: 12.5),
                                          noise: DigestNoise(thisWeek: 1, lastWeek: 3))
                == "Your engagement: 9 this week (up 13% vs last), noise 1 (down 2)")
    }

    @Test("the sparkline scales each day to a block")
    func sparkline() {
        #expect(DigestCard.sparkline([0, 2, 4]) == "▁▅█")
        #expect(DigestCard.sparkline([]) == "")
        #expect(DigestCard.sparkline([0, 0]) == "▁▁")
    }

    @Test("the Changes tab renders loaded, unsupported, failed and loading")
    func rendersEveryState() async throws {
        let loaded = try await LoadedChangesService.make()
        let decisions = LoadedChangesService.decisions()
        ViewHost.render(ChangesView().environmentObject(loaded).environmentObject(decisions))

        let runner = RuleBasedRunner()   // no rules: the read can't run, so the state is .failed
        let failed = ChangesService(scoutctl: { URL(fileURLWithPath: "/usr/bin/scoutctl") }, runner: runner,
                                    logsDirectory: URL(fileURLWithPath: "/tmp/none-\(UUID().uuidString)"),
                                    pendingDirectory: URL(fileURLWithPath: "/tmp/none-\(UUID().uuidString)"),
                                    fileEvents: NoopFS())
        ViewHost.render(ChangesView().environmentObject(failed).environmentObject(decisions))   // .idle
        await failed.refresh()
        #expect(failed.state == .failed("Couldn't run Scout's engine."))
        ViewHost.render(ChangesView().environmentObject(failed).environmentObject(decisions))
    }

    @Test("the banners say what is unconfirmed or still calibrating")
    func bannerCopy() async throws {
        let service = try await LoadedChangesService.make()
        let changes = try #require(service.snapshot).changes
        #expect(changes.verified == false)
        #expect(changes.calibrating == true)
        #expect(ChangesView.bannerTitles(for: changes) == [
            "Scout couldn't check these changes against git just now, so they're shown unconfirmed and Undo is off.",
            "Calibrating: verdicts are shown, but nothing acts on them yet.",
        ])
        #expect(ChangesView.unsupportedMessage == "Update Scout's engine to see this page.")
    }
}
