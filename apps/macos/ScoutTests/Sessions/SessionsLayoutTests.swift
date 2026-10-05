import Foundation
import Testing
@testable import Scout

@Suite("SessionsLayout")
struct SessionsLayoutTests {
    private let now = SessionsFixture.now

    private func ids(_ sessions: [AgentSession]) -> [String] { sessions.map(\.id) }

    @Test func theDefaultFilterHidesScoutRunsAndDoneSessions() throws {
        let index = try SessionsFixture.index()
        let visible = SessionsLayout.visibleSessions(index: index, filter: SessionsFilter(), now: now)
        #expect(Set(ids(visible)) == ["local_A", "local_R", "local_W", "local_C", "local_U", "local_Q",
                                      "cli:aaaaaaaa-0000-0000-0000-000000000007", "local_S"])
    }

    @Test func rowsAreProjectsByRecencyWithCardsBySeverity() throws {
        let index = try SessionsFixture.index()
        let rows = SessionsLayout.rows(index: index, filter: SessionsFilter(), now: now)
        #expect(rows.map(\.name) == ["Example Repo", "other-repo"])
        // needs_you → running → parked (open first, then closed); stale collapses.
        #expect(ids(rows[0].active) == ["local_A", "local_R", "local_C", "local_U"])
        #expect(ids(rows[0].stale) == ["local_S"])
        #expect(rows[0].done.isEmpty)
        #expect(ids(rows[1].active) == ["local_W", "local_Q", "cli:aaaaaaaa-0000-0000-0000-000000000007"])
        #expect(rows[0].counts[.parked] == 2 && rows[0].counts[.stale] == 1)
    }

    @Test func theNowStripIsNeedsYouThenRunningAcrossProjects() throws {
        let index = try SessionsFixture.index()
        #expect(ids(SessionsLayout.nowStrip(index: index, filter: SessionsFilter(), now: now)) == ["local_A", "local_R"])
        var other = SessionsFilter()
        other.projectKey = "/Users/alex/code/other-repo"
        #expect(SessionsLayout.nowStrip(index: index, filter: other, now: now).isEmpty)
    }

    @Test func recentlyDoneShowsOnlyDoneSessionsInsideTheWindow() throws {
        let index = try SessionsFixture.index()
        var filter = SessionsFilter()
        filter.showRecentlyDone = true
        let rows = SessionsLayout.rows(index: index, filter: filter, now: now)
        // local_D was archived 2.5 h ago; local_M's PR merged 3 days ago (window: 24 h).
        #expect(ids(rows.first { $0.name == "Example Repo" }?.done ?? []) == ["local_D"])
        #expect(rows.first { $0.name == "other-repo" }?.done.isEmpty == true)
        #expect(SessionsLayout.stateCounts(index: index, filter: filter, now: now)[.done] == 1)
        #expect(SessionsLayout.stateCounts(index: index, filter: SessionsFilter(), now: now)[.done] == nil)
    }

    @Test func scoutRunsAppearOnlyWhenAskedFor() throws {
        let index = try SessionsFixture.index()
        var filter = SessionsFilter()
        filter.showScoutRuns = true
        let rows = SessionsLayout.rows(index: index, filter: filter, now: now)
        #expect(rows.map(\.name).contains("Scout"))
        #expect(SessionsLayout.needsYouCount(in: index) == 1)
    }

    @Test func stateChipsFilterCardsButNotTheirOwnCounts() throws {
        let index = try SessionsFixture.index()
        var filter = SessionsFilter()
        filter.states = [.parked]
        let rows = SessionsLayout.rows(index: index, filter: filter, now: now)
        #expect(rows.flatMap(\.active).allSatisfy { $0.state == .parked })
        #expect(rows.allSatisfy { $0.stale.isEmpty })
        #expect(SessionsLayout.nowStrip(index: index, filter: filter, now: now).isEmpty)
        let counts = SessionsLayout.stateCounts(index: index, filter: filter, now: now)
        #expect(counts == [.needsYou: 1, .running: 1, .waiting: 1, .parked: 4, .stale: 1])
    }

    @Test(arguments: [
        ("export", ["local_R"]),
        ("#77", ["local_W"]),
        ("w-trace", ["local_S"]),
        ("release checklist", ["cli:aaaaaaaa-0000-0000-0000-000000000007"]),
        ("OTHER-REPO", ["local_W", "local_Q", "cli:aaaaaaaa-0000-0000-0000-000000000007"]),
        ("   ", ["local_A", "local_R", "local_W", "local_C", "local_U", "local_Q",
                 "cli:aaaaaaaa-0000-0000-0000-000000000007", "local_S"]),
    ])
    func searchMatchesTitlePromptBranchPRAndProject(query: String, expected: [String]) throws {
        let index = try SessionsFixture.index()
        var filter = SessionsFilter()
        filter.search = query
        let found = SessionsLayout.tableRows(index: index, filter: filter, now: now).map(\.id)
        #expect(Set(found) == Set(expected))
    }

    @Test func tableRowsDefaultToSeverityThenRecency() throws {
        let index = try SessionsFixture.index()
        let rows = SessionsLayout.tableRows(index: index, filter: SessionsFilter(), now: now)
        #expect(rows.map(\.id) == ["local_A", "local_R", "local_W", "local_C", "local_U", "local_Q",
                                   "cli:aaaaaaaa-0000-0000-0000-000000000007", "local_S"])
        #expect(rows.map(\.severity) == rows.map(\.severity).sorted())
        #expect(rows.first?.projectName == "Example Repo")
    }

    @Test func sessionsThatTieOnEverythingButIdKeepAStableOrder() throws {
        var object = try SessionsFixture.object()
        var sessions = try #require(object["sessions"] as? [[String: Any]])
        let q = try #require(sessions.first { $0["id"] as? String == "local_Q" })
        var twin = q
        twin["id"] = "local_P"  // sorts before "local_Q"
        sessions.append(twin)
        object["sessions"] = sessions
        let index = try SessionIndex.decode(SessionsFixture.encode(object))
        let a = try #require(SessionsFixture.session("local_P", in: index))
        let b = try #require(SessionsFixture.session("local_Q", in: index))
        #expect(SessionsLayout.severityOrder(a, b))
        #expect(!SessionsLayout.severityOrder(b, a))
        #expect(!SessionsLayout.severityOrder(a, a))
        let row = SessionsLayout.rows(index: index, filter: SessionsFilter(), now: now)
            .first { $0.name == "other-repo" }
        #expect(ids(row?.active ?? []).prefix(3) == ["local_W", "local_P", "local_Q"])
    }

    @Test func parentAndChildrenResolveThroughTheIndex() throws {
        let index = try SessionsFixture.index()
        let child = try #require(SessionsFixture.session("local_C", in: index))
        let parent = try #require(SessionsFixture.session("local_A", in: index))
        #expect(SessionsLayout.parent(of: child, in: index)?.id == "local_A")
        #expect(ids(SessionsLayout.children(of: parent, in: index)) == ["local_C"])
        #expect(SessionsLayout.parent(of: parent, in: index) == nil)
    }

    @Test func anUnknownProjectKeyFallsBackToItsBasename() throws {
        let index = try SessionsFixture.index()
        #expect(SessionsLayout.projectName(for: "/Users/alex/code/zed", in: index) == "zed")
        #expect(SessionsLayout.projectName(for: "/Users/alex/code/example-repo", in: index) == "Example Repo")
    }
}
