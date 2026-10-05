import Testing
@testable import Scout

@Suite("ConnectorKeyAlias")
struct ConnectorKeyAliasTests {
    @Test func resolvesSlackLegacyKeyToCanonical() {
        #expect(ConnectorKeyAlias.canonical("mcp:plugin_slack_slack") == "mcp:claude_ai_Slack")
    }

    @Test func resolvesLinearLegacyKeyToCanonical() {
        #expect(ConnectorKeyAlias.canonical("mcp:plugin_linear_linear") == "mcp:claude_ai_Linear")
    }

    @Test func passesThroughUnknownKey() {
        // Brand-new connectors render via the rail card's heuristic prettifier
        // and shouldn't get rewritten here.
        #expect(ConnectorKeyAlias.canonical("mcp:claude_ai_NewThing") == "mcp:claude_ai_NewThing")
        #expect(ConnectorKeyAlias.canonical("bash:ls") == "bash:ls")
        #expect(ConnectorKeyAlias.canonical("") == "")
    }

    @Test func canonicalKeysAreIdempotent() {
        for (_, canonical) in ConnectorKeyAlias.legacyAliases {
            #expect(ConnectorKeyAlias.canonical(canonical) == canonical,
                    "canonical key \(canonical) must not be re-aliased")
        }
    }
}
