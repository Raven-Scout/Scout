import Foundation
import AppKit

enum GhosttyLauncher {
    static let bundleID = "com.mitchellh.ghostty"

    /// Open a new Ghostty **tab** at ``cwd`` running ``runningCommand``.
    ///
    /// Ghostty's ``-e`` CLI flag always opens a new *window* — there's no
    /// "new tab" CLI equivalent. The only reliable way to get a tab in the
    /// user's existing Ghostty window is to drive it via Accessibility:
    /// activate the app, press ``⌘T`` to open a new tab, then type the
    /// shell line and press return.
    ///
    /// Requires Accessibility permission for Scout.app; macOS will prompt
    /// on first use. Every failure throws a `ClaudeLauncher.LaunchError` the
    /// caller shows (UI audit #38): Ghostty missing, macOS refusing to open
    /// it, or System Events refusing the keystrokes (`accessibilityDenied`,
    /// whose alert opens the Accessibility pane).
    static func openNewTab(cwd: URL, runningCommand: String) async throws {
        let runningGhostty = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).first

        if let app = runningGhostty {
            app.activate()
        } else if let appURL = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID) {
            let config = NSWorkspace.OpenConfiguration()
            config.activates = true
            do {
                _ = try await NSWorkspace.shared.openApplication(at: appURL, configuration: config)
            } catch {
                throw ClaudeLauncher.LaunchError.ghosttyLaunchFailed(error.localizedDescription)
            }
        } else {
            throw ClaudeLauncher.LaunchError.ghosttyNotInstalled
        }

        // Give Ghostty longer to come up when we had to cold-launch it —
        // the first window needs to exist before ⌘T has a target.
        let script = makeNewTabScript(cwd: cwd, runningCommand: runningCommand,
                                      delay: runningGhostty == nil ? 0.9 : 0.3)
        // The script sleeps (`delay`), so it runs off the main thread.
        try await Task.detached(priority: .userInitiated) {
            try ClaudeLauncher.runAppleScript(script)
        }.value
    }

    /// The System Events script that opens the tab and types the command.
    /// Pure, so the escaping is unit-tested.
    nonisolated static func makeNewTabScript(cwd: URL, runningCommand: String, delay: Double) -> String {
        let cwdEscaped = cwd.path.replacingOccurrences(of: "\"", with: "\\\"")
        let shellLine = "cd \"\(cwdEscaped)\" && \(runningCommand)"
        // Escape for embedding inside an AppleScript string literal.
        let asEscaped = ClaudeLauncher.appleScriptEscape(shellLine)
        return """
        delay \(delay)
        tell application "System Events"
          tell process "Ghostty"
            set frontmost to true
            keystroke "t" using command down
            delay 0.2
            keystroke "\(asEscaped)"
            keystroke return
          end tell
        end tell
        """
    }
}
