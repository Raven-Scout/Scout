import Testing
import Foundation
@testable import Scout

@Suite("ClaudeCodeCLI")
struct ClaudeCodeCLITests {
    @Test func argvBuilders() {
        #expect(ClaudeCodeCLI.marketplaceAdd(path: URL(fileURLWithPath: "/Users/alex/.local/share/scout/engine/current")) == ["plugin", "marketplace", "add", "/Users/alex/.local/share/scout/engine/current"])
        #expect(ClaudeCodeCLI.pluginInstall == ["plugin", "install", "scout@scout-plugin"])
        #expect(ClaudeCodeCLI.pluginUpdate == ["plugin", "update", "scout@scout-plugin"])
        #expect(ClaudeCodeCLI.marketplaceUpdate == ["plugin", "marketplace", "update", "scout-plugin"])
        #expect(ClaudeCodeCLI.authStatus == ["auth", "status", "--json"])
    }

    // Fixtures load by name, not by subfolder — the test bundle flattens
    // resources, so a `Fixtures/claude-plugins` subpath does not resolve there.
    @Test func parsesAuthStatus() throws {
        let url = Bundle(for: FixtureAnchor.self).url(forResource: "claude-auth-status", withExtension: "json")!
        let s = ClaudeCodeCLI.parseAuthStatus(try Data(contentsOf: url))
        #expect(s == ClaudeCodeCLI.AuthStatus(loggedIn: true, authMethod: "claude.ai", subscriptionType: "team"))
        #expect(ClaudeCodeCLI.parseAuthStatus(Data("nope".utf8)) == nil)
    }

    @Test func parsesVersionLine() {
        #expect(ClaudeCodeCLI.parseVersion(Data("2.1.259 (Claude Code)\n".utf8)) == "2.1.259")
        #expect(ClaudeCodeCLI.parseVersion(Data("".utf8)) == nil)
    }

    @Test func handOffCommands() {
        #expect(ClaudeCodeCLI.installCommand == "curl -fsSL https://claude.ai/install.sh | bash")
        #expect(ClaudeCodeCLI.loginCommand(claude: URL(fileURLWithPath: "/Users/alex/.local/bin/claude")) == "\"/Users/alex/.local/bin/claude\" auth login")
    }
}
