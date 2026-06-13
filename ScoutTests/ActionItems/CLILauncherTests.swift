import Testing
import Foundation
@testable import Scout

@Suite("CLILauncher")
struct CLILauncherTests {

    // MARK: - Model

    @Test func cliTerminalRawValuesAreStableAppStorageContract() {
        #expect(CLITerminal.auto.rawValue == "auto")
        #expect(CLITerminal.terminalApp.rawValue == "terminalApp")
        #expect(CLITerminal.iterm2.rawValue == "iterm2")
        #expect(CLITerminal.custom.rawValue == "custom")
        #expect(CLITerminal(rawValue: "auto") == .auto)
        #expect(CLITerminal(rawValue: "nonsense") == nil)
        #expect(CLITerminal.allCases.count == 4)
    }

    @Test func cliConfigAutoDefault() {
        let c = CLIConfig.auto
        #expect(c.claudePathOverride == "")
        #expect(c.terminal == .auto)
        #expect(c.customCommand == "")
    }
}
