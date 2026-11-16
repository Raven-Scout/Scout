import Testing
import Foundation
@testable import Scout

/// `connector-alerts.json` is written by the engine
/// (`scout.scripts.connector_health_report.render_alerts_state`) and read by
/// `ConnectorAlert.parseFile`. `Fixtures/connector-alerts.json` is a byte copy
/// of the engine's generated `plugin/engine/tests/fixtures/contract/connector-alerts.json`
/// (`contract.yml` compares them), so these pins break if either side moves.
@Suite("ConnectorAlert contract")
struct ConnectorAlertContractTests {
    private static func fixtureURL() throws -> URL {
        try #require(Bundle(for: FixtureAnchor.self).url(forResource: "connector-alerts", withExtension: "json"))
    }

    private static func date(_ s: String) -> Date {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.date(from: s)!
    }

    @Test func parsesEveryAlertTheEngineWrites() throws {
        let alerts = ConnectorAlert.parseFile(at: try Self.fixtureURL())
        #expect(alerts.count == 2)

        let slack = try #require(alerts.first { $0.connector == "mcp:claude_ai_Slack" })
        #expect(slack.level == .critical)
        #expect(slack.ts == Self.date("2026-04-28T13:00:00Z"))
        // A sustained alert keeps the first run's timestamp, so its ack
        // fingerprint is stable across runs.
        #expect(slack.firstSeen == Self.date("2026-04-27T13:00:00Z"))
        #expect(slack.fingerprint == "mcp:claude_ai_Slack|CRITICAL|2026-04-27T13:00:00Z")
        #expect(slack.reason.hasPrefix("2 failed call(s) in `morning-briefing` run"))

        let linear = try #require(alerts.first { $0.connector == "mcp:claude_ai_Linear" })
        #expect(linear.level == .warning)
        #expect(linear.firstSeen == linear.ts)
        #expect(linear.reason == "3/4 calls errored this run (75%)")
    }

    @Test func canonicalisesLegacyConnectorKeys() {
        let json = """
        {"schema_version": 1, "alerts": [{"connector_key": "mcp:plugin_slack_slack", "level": "CRITICAL",
          "name": "Slack", "reason": "r", "ts": "2026-04-28T13:00:00Z", "first_seen": "2026-04-28T13:00:00Z"}]}
        """
        #expect(ConnectorAlert.parse(Data(json.utf8)).map(\.connector) == ["mcp:claude_ai_Slack"])
    }

    @Test func skipsMalformedEntriesButKeepsTheirSiblings() {
        let json = """
        {"schema_version": 1, "alerts": [
          "not an object",
          {"connector_key": "mcp:a", "level": "NOTICE", "ts": "2026-04-28T13:00:00Z", "first_seen": "2026-04-28T13:00:00Z"},
          {"connector_key": "mcp:b", "level": "WARNING", "ts": "yesterday", "first_seen": "2026-04-28T13:00:00Z"},
          {"level": "WARNING", "ts": "2026-04-28T13:00:00Z", "first_seen": "2026-04-28T13:00:00Z"},
          {"connector_key": "mcp:c", "level": "WARNING", "ts": "2026-04-28T13:00:00Z", "first_seen": "2026-04-28T13:00:00Z"}
        ]}
        """
        #expect(ConnectorAlert.parse(Data(json.utf8)).map(\.connector) == ["mcp:c"])
    }

    @Test func rejectsAnUnknownSchemaOrAMalformedFile() {
        let entry = """
        {"connector_key": "mcp:c", "level": "WARNING", "ts": "2026-04-28T13:00:00Z", "first_seen": "2026-04-28T13:00:00Z"}
        """
        #expect(ConnectorAlert.parse(Data("{\"schema_version\": 2, \"alerts\": [\(entry)]}".utf8)).isEmpty)
        #expect(ConnectorAlert.parse(Data("{\"alerts\": [\(entry)]}".utf8)).isEmpty)
        #expect(ConnectorAlert.parse(Data("not json".utf8)).isEmpty)
        #expect(ConnectorAlert.parse(Data("{\"schema_version\": 1, \"alerts\": []}".utf8)).isEmpty)
        let missing = FileManager.default.temporaryDirectory
            .appendingPathComponent("absent-\(UUID().uuidString).json")
        #expect(ConnectorAlert.parseFile(at: missing).isEmpty)
    }

    /// The pipe-delimited format of the retired bash report and the engine's
    /// human-readable `.log` line are both not the app's input.
    @Test func ignoresTheHumanReadableLogFormats() {
        let bash = "2026-04-22T12:30:00Z | CRITICAL | mcp:a | r | first_seen=2026-04-22T12:00:00Z\n"
        let engineLog = "2026-04-22 08:30:00 EDT [CRITICAL] run=briefing Slack: r\n"
        #expect(ConnectorAlert.parse(Data(bash.utf8)).isEmpty)
        #expect(ConnectorAlert.parse(Data(engineLog.utf8)).isEmpty)
    }
}
