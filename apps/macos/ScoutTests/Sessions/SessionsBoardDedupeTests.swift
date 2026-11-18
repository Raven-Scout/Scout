import Foundation
import Testing
@testable import Scout

/// UI audit R4: "Now" cards aren't repeated in their project's lane, and two
/// projects with the same name can be told apart everywhere the page names
/// a project.
@Suite("Sessions board — Now and project names")
struct SessionsBoardDedupeTests {
    private let now = SessionsFixture.now
    private let home = "/Users/alex"

    // MARK: Now strip

    @Test func noSessionIsInBothTheNowStripAndALane() throws {
        let index = try SessionsFixture.index()
        var everything = SessionsFilter()
        everything.showScoutRuns = true
        everything.showRecentlyDone = true
        for filter in [SessionsFilter(), everything] {
            let strip = Set(SessionsLayout.nowStrip(index: index, filter: filter, now: now).map(\.id))
            let lanes = SessionsLayout.rows(index: index, filter: filter, now: now)
                .flatMap { $0.active + $0.stale + $0.done }.map(\.id)
            #expect(!strip.isEmpty)
            #expect(strip.isDisjoint(with: lanes))
            // Every visible session is shown exactly once.
            #expect(strip.count + lanes.count
                    == SessionsLayout.visibleSessions(index: index, filter: filter, now: now).count)
        }
    }

    @Test func aProjectWhoseSessionsAreAllInNowHasNoLane() throws {
        var filter = SessionsFilter()
        filter.states = [.needsYou, .running]
        let index = try SessionsFixture.index()
        #expect(SessionsLayout.nowStrip(index: index, filter: filter, now: now).map(\.id) == ["local_A", "local_R"])
        #expect(SessionsLayout.rows(index: index, filter: filter, now: now).isEmpty)
    }

    @Test func theTableStillListsNowSessions() throws {
        let index = try SessionsFixture.index()
        let rows = SessionsLayout.tableRows(index: index, filter: SessionsFilter(), now: now).map(\.id)
        #expect(rows.prefix(2) == ["local_A", "local_R"])
    }

    // MARK: Same-named projects

    @Test func uniqueNamesReadAsWritten() {
        let names = DisplayName.projectNames(
            [("/Users/alex/code/example-repo", "Example Repo"), ("/Users/alex/Scout", "Scout")], home: home)
        #expect(names == ["/Users/alex/code/example-repo": "Example Repo", "/Users/alex/Scout": "Scout"])
    }

    @Test func sameNamedFoldersGetTheShortestPathThatDiffers() {
        let names = DisplayName.projectNames(
            [("/Users/alex/Scout", "Scout"), ("/Users/alex/code/Scout", "Scout")], home: home)
        #expect(names["/Users/alex/Scout"] == "Scout (~/Scout)")
        #expect(names["/Users/alex/code/Scout"] == "Scout (code/Scout)")
    }

    @Test func aDifferentFolderNameIsEnough() {
        let names = DisplayName.projectNames(
            [("/Users/alex/code/scout-plugin", "Scout"), ("/Users/alex/Scout", "scout")], home: home)
        // Names compare without case, so these two clash too.
        #expect(names["/Users/alex/code/scout-plugin"] == "Scout (scout-plugin)")
        #expect(names["/Users/alex/Scout"] == "scout (Scout)")
    }

    @Test func pathsOutsideHomeAndRepeatedKeysAreHandled() {
        let names = DisplayName.projectNames(
            [("/srv/a/app", "App"), ("/srv/b/app", "App"), ("/srv/a/app", "Ignored"), ("cloud-group", "App")],
            home: home)
        #expect(names["/srv/a/app"] == "App (a/app)")
        #expect(names["/srv/b/app"] == "App (b/app)")
        #expect(names["cloud-group"] == "App (cloud-group)")
        #expect(Set(names.values).count == 3)
    }

    private func twoScoutLanes() throws -> SessionIndex {
        var second = try #require(try SessionsFixture.object()["sessions"] as? [[String: Any]])
            .first { $0["id"] as? String == "local_W" } ?? [:]
        second["id"] = "local_W2"
        second["project_key"] = "/Users/alex/code/Scout"
        second["cwd"] = "/Users/alex/code/Scout"
        second["origin_cwd"] = "/Users/alex/code/Scout"
        second["cli_session_id"] = "aaaaaaaa-0000-0000-0000-000000000099"
        return try SessionsFixture.index(
            patching: [:],
            addingProjects: [["key": "/Users/alex/code/Scout", "name": "Scout", "group_id": NSNull(),
                              "counts": ["waiting": 1]]],
            addingSessions: [second])
    }

    @Test func lanesMenuAndTableTellTwoScoutProjectsApart() throws {
        let index = try twoScoutLanes()
        var filter = SessionsFilter()
        filter.showScoutRuns = true
        let laneNames = SessionsLayout.rows(index: index, filter: filter, now: now).map(\.name)
        let scoutLanes = laneNames.filter { $0.hasPrefix("Scout") }
        #expect(scoutLanes.count == 2)
        #expect(Set(scoutLanes).count == 2)
        #expect(SessionsLayout.menuProjects(index: index, home: home).map(\.name).filter { $0.hasPrefix("Scout") }
                == ["Scout (~/Scout)", "Scout (code/Scout)"])
        #expect(SessionsLayout.projectName(for: "/Users/alex/code/Scout", in: index, home: home) == "Scout (code/Scout)")
        let tableNames = Set(SessionsLayout.tableRows(index: index, filter: filter, now: now)
            .filter { $0.projectName.hasPrefix("Scout") }.map(\.projectName))
        #expect(tableNames.count == 2)
        // Searching the shared name still finds both.
        filter.search = "scout"
        #expect(Set(SessionsLayout.tableRows(index: index, filter: filter, now: now).map(\.id))
            .isSuperset(of: ["local_SR", "local_W2"]))
    }

    @Test func aProjectNamedOnlyBySessionsStillGetsItsFolderName() throws {
        let index = try SessionsFixture.index()
        #expect(SessionsLayout.projectNames(in: index, home: home)["/Users/alex/code/other-repo"] == "other-repo")
    }
}
