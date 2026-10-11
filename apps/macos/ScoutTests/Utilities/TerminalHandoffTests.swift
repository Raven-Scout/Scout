import Testing
@testable import Scout

/// Script construction only: no test ever runs AppleScript or opens Terminal
/// (the onboarding model takes the handoff as an injected closure).
@Suite("TerminalHandoff")
struct TerminalHandoffTests {
    @Test func scriptRunsTheCommandVisiblyInTerminal() {
        let s = TerminalHandoff.makeScript(command: "curl -fsSL https://claude.ai/install.sh | bash")
        #expect(s.contains("tell application \"Terminal\""))
        #expect(s.contains("activate"))
        #expect(s.contains("do script \"curl -fsSL https://claude.ai/install.sh | bash\""))
    }

    @Test func escapesQuotesForAppleScript() {
        let s = TerminalHandoff.makeScript(command: "\"/Users/alex/.local/bin/claude\" auth login")
        #expect(s.contains(#"do script "\"/Users/alex/.local/bin/claude\" auth login""#))
    }

    @Test func escapesBackslashesBeforeQuotes() {
        let s = TerminalHandoff.makeScript(command: #"echo a\"b"#)
        #expect(s.contains(#"do script "echo a\\\"b""#))
    }
}
