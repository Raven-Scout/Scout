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

    // MARK: - claude path resolution

    @Test func resolveClaudePath_executableOverrideWins() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let exe = dir.appendingPathComponent("claude")
        try "#!/bin/sh\n".write(to: exe, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes(
            [.posixPermissions: NSNumber(value: 0o755)], ofItemAtPath: exe.path)

        #expect(ClaudeLauncher.resolveClaudePath(override: exe.path) == exe.path)
    }

    @Test func resolveClaudePath_nonExecutableOverrideReturnsNil() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let notExe = dir.appendingPathComponent("claude.txt")
        try "not executable".write(to: notExe, atomically: true, encoding: .utf8)

        // A non-empty but non-executable override must NOT silently fall back
        // to the probe — it returns nil so the caller surfaces the typo.
        #expect(ClaudeLauncher.resolveClaudePath(override: notExe.path) == nil)
        #expect(ClaudeLauncher.resolveClaudePath(override: "/no/such/path/claude") == nil)
    }
}
