import Foundation
import Testing
@testable import Scout

@Suite("SessionsFormat")
struct SessionsFormatTests {
    private let now = SessionsFixture.now

    @Test(arguments: [(40.0, "40s ago"), (720, "12m ago"), (7200, "2h ago"), (259_200, "3d ago"), (-5, "0s ago")])
    func agesUseTheEngineSpelling(secondsAgo: Double, expected: String) {
        #expect(SessionsFormat.ago(now.addingTimeInterval(-secondsAgo), now: now) == expected)
    }

    @Test func aMissingDateIsADash() {
        #expect(SessionsFormat.ago(nil, now: now) == "—")
    }

    @Test func prChipsCarryReviewAndChecks() throws {
        let index = try SessionsFixture.index()
        func chip(_ id: String) -> String? { SessionsFixture.session(id, in: index)?.pr.map(SessionsFormat.prChip) }
        #expect(chip("local_A") == "#98 · changes requested · ✗")
        #expect(chip("local_R") == "#102 · review required · ✓")
        #expect(chip("local_Q") == "#81 · draft · …")
        #expect(chip("local_U") == "#110")
        #expect(chip("local_M") == "#60 · merged · ✓")
    }

    @Test func freshnessNamesALonePRProblemAndCountsSeveral() {
        let refreshed = now.addingTimeInterval(-10)
        let checked = now.addingTimeInterval(-120)
        #expect(SessionsFormat.freshness(lastRefreshAt: refreshed, prFinishedAt: checked, prErrors: [], now: now)
                == "updated 10s ago · PRs checked 2m ago")
        #expect(SessionsFormat.freshness(lastRefreshAt: refreshed, prFinishedAt: checked,
                                         prErrors: ["gh not found on PATH — PR states unknown"], now: now)
                == "updated 10s ago · PR check failed: gh not found on PATH — PR states unknown")
        #expect(SessionsFormat.freshness(lastRefreshAt: nil, prFinishedAt: checked,
                                         prErrors: ["pr view failed: example-org/example-repo#98", "pr view failed: example-org/example-repo#102"],
                                         now: now)
                == "PR check: 2 problems")
        #expect(SessionsFormat.freshness(lastRefreshAt: nil, prFinishedAt: nil, prErrors: [], now: now)
                == "Reading your Claude Code sessions…")
    }

    @Test func modelsLoseTheirClaudePrefix() {
        #expect(SessionsFormat.shortModel("claude-opus-5") == "opus-5")
        #expect(SessionsFormat.shortModel("gpt-x") == "gpt-x")
    }

    @Test func resumeRunsInTheWorktreeThenTheProjectThenHome() throws {
        let a = try #require(SessionsFixture.session("local_A", in: SessionsFixture.index()))
        #expect(SessionsFormat.resumeDirectory(for: a, exists: { _ in true }).path == a.cwd)
        #expect(SessionsFormat.resumeDirectory(for: a, exists: { $0 == a.originCwd }).path == a.originCwd)
        #expect(SessionsFormat.resumeDirectory(for: a, exists: { _ in false })
                == FileManager.default.homeDirectoryForCurrentUser)
    }
}
