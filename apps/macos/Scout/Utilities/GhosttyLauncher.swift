import Foundation
import AppKit

enum GhosttyLauncher {
    /// Open a new Ghostty **tab** at ``cwd`` running ``runningCommand``.
    ///
    /// Ghostty's ``-e`` CLI flag always opens a new *window* — there's no
    /// "new tab" CLI equivalent. The only reliable way to get a tab in the
    /// user's existing Ghostty window is to drive it via Accessibility:
    /// activate the app, press ``⌘T`` to open a new tab, then type the
    /// shell line and press return.
    ///
    /// Requires Accessibility permission for Scout.app; macOS will prompt
    /// on first use. If the permission is denied, the keystrokes silently
    /// no-op (we ``NSLog`` the AppleScript error for Console debugging).
    static func openNewTab(cwd: URL, runningCommand: String) {
        let cwdEscaped = cwd.path.replacingOccurrences(of: "\"", with: "\\\"")
        let shellLine = "cd \"\(cwdEscaped)\" && \(runningCommand)"

        // Escape for embedding inside an AppleScript string literal.
        // Backslash first so quote-escaping doesn't double-escape.
        let asEscaped = shellLine
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")

        let runningGhostty = NSRunningApplication.runningApplications(
            withBundleIdentifier: "com.mitchellh.ghostty"
        ).first

        if let app = runningGhostty {
            app.activate()
        } else if let appURL = NSWorkspace.shared.urlForApplication(
            withBundleIdentifier: "com.mitchellh.ghostty"
        ) {
            let config = NSWorkspace.OpenConfiguration()
            config.activates = true
            NSWorkspace.shared.openApplication(at: appURL, configuration: config) { _, error in
                if let error {
                    NSLog("GhosttyLauncher: NSWorkspace.openApplication failed: \(error.localizedDescription)")
                }
            }
        } else {
            NSLog("GhosttyLauncher: Ghostty.app not found")
            return
        }

        // Give Ghostty longer to come up when we had to cold-launch it —
        // the first window needs to exist before ⌘T has a target.
        let delay = runningGhostty == nil ? 0.9 : 0.3

        let script = """
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

        DispatchQueue.global().async {
            var errorDict: NSDictionary?
            _ = NSAppleScript(source: script)?.executeAndReturnError(&errorDict)
            if let err = errorDict {
                NSLog("GhosttyLauncher: AppleScript error: \(err)")
            }
        }
    }
}
