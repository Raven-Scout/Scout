import Testing
import Foundation
@testable import Scout

@Suite("ConnectorHealthMatrix")
struct ConnectorHealthMatrixTests {
    @Test func aggregatesCellsAcrossSessionsAndConnectors() {
        // Fixture intentionally contains legacy `mcp:plugin_slack_slack` and
        // `mcp:plugin_linear_linear` rows — `ConnectorCall.parseFile` runs
        // them through `ConnectorKeyAlias.canonical(_:)` so the matrix sees
        // canonical keys. CC-7: this is what prevents legacy rows from
        // orphaning during the 14-day window after a rename.
        let calls = Self.fixtureCalls()
        let matrix = ConnectorHealthMatrix(
            calls: calls,
            connectors: ["mcp:claude_ai_Slack",
                         "mcp:claude_ai_Gmail",
                         "mcp:claude_ai_Google_Drive",
                         "mcp:claude_ai_Linear"]
        )

        let sessions = matrix.sessionsNewestFirst
        #expect(sessions.map(\.id) == ["s3", "s2", "s1"])

        // Slack: s1 ok, s2 ok, s3 absent — legacy log key resolved to canonical.
        #expect(matrix.cell(connector: "mcp:claude_ai_Slack", sessionId: "s1") == .ok(count: 1))
        #expect(matrix.cell(connector: "mcp:claude_ai_Slack", sessionId: "s2") == .ok(count: 1))
        #expect(matrix.cell(connector: "mcp:claude_ai_Slack", sessionId: "s3") == .absent)
        // Gmail: s1 ok, s2 absent, s3 partial (2 ok, 1 err)
        #expect(matrix.cell(connector: "mcp:claude_ai_Gmail", sessionId: "s3") == .partial(ok: 2, total: 3))
        // Drive: s2 all-error
        #expect(matrix.cell(connector: "mcp:claude_ai_Google_Drive", sessionId: "s2") == .error)
        // Linear: s3 ok — also resolved from a legacy `plugin_linear_linear` row.
        #expect(matrix.cell(connector: "mcp:claude_ai_Linear", sessionId: "s3") == .ok(count: 1))
    }

    @Test func sevenDayRateIgnoresAbsentSessions() {
        let calls = Self.fixtureCalls()
        let matrix = ConnectorHealthMatrix(
            calls: calls,
            connectors: ["mcp:claude_ai_Slack", "mcp:claude_ai_Google_Drive"]
        )
        // Slack: 2 ok / 2 total = 100% (after legacy → canonical resolution).
        #expect(matrix.successRate(connector: "mcp:claude_ai_Slack") == 1.0)
        // Drive: 0 ok / 3 total = 0%
        #expect(matrix.successRate(connector: "mcp:claude_ai_Google_Drive") == 0.0)
    }

    private static func fixtureCalls() -> [ConnectorCall] {
        let url = Bundle(for: FixtureAnchor.self).resourceURL!
            .appendingPathComponent("connector-calls-2026-04-22.jsonl")
        return ConnectorCall.parseFile(at: url)
    }
}
