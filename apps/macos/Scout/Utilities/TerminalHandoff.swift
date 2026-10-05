import Foundation

/// Opens Terminal.app running one command, visibly, in front of the user
/// (spec §4.3): the app never runs Anthropic's installer or a login flow
/// itself. Reuses ClaudeLauncher's AppleScript escaping and runner.
nonisolated enum TerminalHandoff {
    static func makeScript(command: String) -> String {
        """
        tell application "Terminal"
          activate
          do script "\(ClaudeLauncher.appleScriptEscape(command))"
        end tell
        """
    }

    @MainActor
    static func run(_ command: String) throws {
        try ClaudeLauncher.runAppleScript(makeScript(command: command))
    }
}
