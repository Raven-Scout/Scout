import Testing
import Foundation
@testable import Scout

@Suite("ConnectorDetection")
struct ConnectorDetectionTests {
    @Test func decodesScoutctlConnectorsDetect() {
        let json = #"{"email": {"status": "connected", "needs_user_input": [], "evidence": "claude.ai Gmail: … - ✔ Connected"}, "slack": {"status": "needs_auth", "needs_user_input": ["user_slack_id"], "evidence": "…"}, "fathom": {"status": "unknown", "needs_user_input": [], "evidence": "no matching MCP server"}, "drive": {"status": "unavailable", "needs_user_input": [], "evidence": ""}}"#
        let d = ConnectorDetection.parse(Data(json.utf8))
        #expect(d?["email"]?.status == .connected)
        #expect(d?["slack"]?.status == .needsAuth && d?["slack"]?.needsUserInput == ["user_slack_id"])
        #expect(d?["fathom"]?.status == .unknown)
        #expect(d?["drive"]?.status == .unavailable)
        #expect(ConnectorDetection.parse(Data("x".utf8)) == nil)
    }

    /// An unknown status value fails the whole decode rather than guessing.
    @Test func rejectsAnUnknownStatus() {
        let json = #"{"email": {"status": "sideways", "needs_user_input": [], "evidence": ""}}"#
        #expect(ConnectorDetection.parse(Data(json.utf8)) == nil)
    }

    /// The labels cover every connector the engine's shipped probe registry
    /// (`plugin/templates/connector-probes.yaml`) knows.
    @Test func displayNamesCoverTheShippedRegistry() {
        for key in ["slack", "calendar", "email", "linear", "github", "granola", "fathom", "drive", "claude_sessions"] {
            #expect(ConnectorDetection.displayNames[key] != nil, "missing label for \(key)")
        }
    }
}
