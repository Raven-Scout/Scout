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

    /// E4: "unmappable is unknown". A status the app doesn't know, or a
    /// missing one, becomes `.unknown`; missing optional fields get defaults.
    @Test func unmappableStatusAndMissingFieldsDecodeTolerantly() {
        let json = #"{"email": {"status": "sideways", "needs_user_input": [], "evidence": "e"}, "slack": {"status": "connected"}, "linear": {"evidence": "x"}, "drive": {"status": 7, "needs_user_input": "nope"}}"#
        let d = ConnectorDetection.parse(Data(json.utf8))
        #expect(d?["email"] == ConnectorDetection(status: .unknown, needsUserInput: [], evidence: "e"))
        #expect(d?["slack"] == ConnectorDetection(status: .connected, needsUserInput: [], evidence: ""))
        #expect(d?["linear"] == ConnectorDetection(status: .unknown, needsUserInput: [], evidence: "x"))
        #expect(d?["drive"] == ConnectorDetection(status: .unknown, needsUserInput: [], evidence: ""))
    }

    /// One unreadable entry is dropped; the rest of the map survives.
    @Test func oneBadEntryDoesNotFailTheMap() {
        let json = #"{"email": {"status": "connected", "needs_user_input": [], "evidence": ""}, "slack": 5, "github": ["x"]}"#
        let d = ConnectorDetection.parse(Data(json.utf8))
        #expect(d?.keys.sorted() == ["email"])
        #expect(d?["email"]?.status == .connected)
        #expect(ConnectorDetection.parse(Data("[1, 2]".utf8)) == nil)
    }

    /// The labels cover every connector the engine's shipped probe registry
    /// (`plugin/templates/connector-probes.yaml`) knows.
    @Test func displayNamesCoverTheShippedRegistry() {
        for key in ["slack", "calendar", "email", "linear", "github", "granola", "fathom", "drive", "claude_sessions"] {
            #expect(ConnectorDetection.displayNames[key] != nil, "missing label for \(key)")
        }
    }
}
