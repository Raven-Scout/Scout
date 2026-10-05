import Foundation
import Testing
@testable import Scout

/// Every launch failure is surfaced to the user in an alert, so each case must
/// carry an actionable `errorDescription` — these are the strings the user
/// actually reads when a terminal or Claude Desktop isn't where we expected.
@Suite("ClaudeLauncher — launch errors")
struct ClaudeLauncherErrorTests {

    private var allCases: [ClaudeLauncher.LaunchError] {
        [
            .ghosttyNotInstalled,
            .claudeDesktopNotInstalled,
            .claudeCLINotFound,
            .scriptWriteFailed("permission denied"),
            .urlBuildFailed,
            .iterm2NotInstalled,
            .terminalLaunchFailed("not authorized"),
            .customCommandEmpty,
        ]
    }

    @Test("every case has a non-empty description")
    func everyCaseDescribesItself() {
        for error in allCases {
            let text = error.errorDescription ?? ""
            #expect(!text.isEmpty, "\(error) has no errorDescription")
            // LocalizedError must also drive `localizedDescription`.
            #expect(error.localizedDescription == text)
        }
    }

    @Test("install failures point at where to get the missing app")
    func installErrorsCarryDownloadPointers() {
        #expect(ClaudeLauncher.LaunchError.ghosttyNotInstalled
            .errorDescription?.contains("ghostty.org") == true)
        #expect(ClaudeLauncher.LaunchError.claudeDesktopNotInstalled
            .errorDescription?.contains("claude.ai/download") == true)
        #expect(ClaudeLauncher.LaunchError.iterm2NotInstalled
            .errorDescription?.contains("iterm2.com") == true)
        #expect(ClaudeLauncher.LaunchError.claudeCLINotFound
            .errorDescription?.contains("which claude") == true)
    }

    @Test("errors that wrap an underlying message pass it through")
    func wrappedMessagesArePreserved() {
        #expect(ClaudeLauncher.LaunchError.scriptWriteFailed("disk full")
            .errorDescription?.contains("disk full") == true)
        #expect(ClaudeLauncher.LaunchError.terminalLaunchFailed("osascript denied")
            .errorDescription?.contains("osascript denied") == true)
    }

    @Test("a terminal failure hints at the Automation permission")
    func terminalFailureMentionsAutomationPermission() {
        let text = ClaudeLauncher.LaunchError.terminalLaunchFailed("x").errorDescription ?? ""
        #expect(text.contains("Automation"))
        #expect(text.contains("Privacy & Security"))
    }

    @Test("the empty-custom-command error names the placeholders")
    func customCommandEmptyExplainsPlaceholders() {
        let text = ClaudeLauncher.LaunchError.customCommandEmpty.errorDescription ?? ""
        #expect(text.contains("{cwd}"))
        #expect(text.contains("{claude}"))
    }

    // MARK: - escaping helpers

    @Test("AppleScript escaping covers backslashes and double quotes")
    func appleScriptEscape_escapesBackslashAndQuote() {
        #expect(ClaudeLauncher.appleScriptEscape("plain") == "plain")
        #expect(ClaudeLauncher.appleScriptEscape(#"say "hi""#) == #"say \"hi\""#)
        #expect(ClaudeLauncher.appleScriptEscape(#"a\b"#) == #"a\\b"#)
        // Backslash is escaped first, so a literal \" becomes \\\"
        #expect(ClaudeLauncher.appleScriptEscape(#"a\"b"#) == #"a\\\"b"#)
    }

    @Test("double-quote escaping covers backslashes and double quotes")
    func shellDoubleQuoteEscape_escapesBackslashAndQuote() {
        #expect(ClaudeLauncher.shellDoubleQuoteEscape("plain") == "plain")
        #expect(ClaudeLauncher.shellDoubleQuoteEscape(#"a"b"#) == #"a\"b"#)
        #expect(ClaudeLauncher.shellDoubleQuoteEscape(#"a\b"#) == #"a\\b"#)
        #expect(ClaudeLauncher.shellDoubleQuoteEscape("") == "")
    }

    @Test("an empty custom template expands to nothing")
    func expandCustomCommand_emptyTemplate() {
        #expect(ClaudeLauncher.expandCustomCommand(
            template: "", claudePath: "/c", cwd: "/w") == "")
    }

    @Test("a template with no placeholders is passed through untouched")
    func expandCustomCommand_noPlaceholders() {
        #expect(ClaudeLauncher.expandCustomCommand(
            template: "open -a Terminal", claudePath: "/c", cwd: "/w") == "open -a Terminal")
    }

    @Test("shell quoting survives a path containing a double quote")
    func shellQuote_handlesDoubleQuotes() {
        // Single-quoting means a double quote needs no escaping at all.
        #expect(ClaudeLauncher.shellQuote(#"/a"b"#) == #"'/a"b'"#)
        #expect(ClaudeLauncher.shellQuote("") == "''")
    }

    // MARK: - target values

    @Test("launch targets carry their configuration")
    func targets_carryConfiguration() {
        let cwd = URL(fileURLWithPath: "/Users/alex/Scout")
        let config = CLIConfig.auto
        if case let .cli(url, cfg) = ClaudeLauncher.Target.cli(cwd: cwd, config: config) {
            #expect(url == cwd)
            #expect(cfg.terminal == .auto)
        } else {
            Issue.record("expected a .cli target")
        }

        guard case let .claudeDesktop(mode) = ClaudeLauncher.Target.claudeDesktop(.cowork) else {
            Issue.record("expected a .claudeDesktop target")
            return
        }
        guard case .cowork = mode else {
            Issue.record("expected the .cowork desktop mode")
            return
        }
    }

    @Test("the code desktop mode carries the session's working folder")
    func desktopCodeModeCarriesFolder() {
        let folder = URL(fileURLWithPath: "/Users/alex/Scout")
        guard case let .claudeDesktop(mode) = ClaudeLauncher.Target.claudeDesktop(.code(folder: folder)),
              case let .code(carried) = mode else {
            Issue.record("expected a .claudeDesktop(.code) target")
            return
        }
        #expect(carried == folder)
    }

    @Test("the three desktop modes are distinguishable")
    func desktopModes_areDistinct() {
        // `.code` carries an associated value, so the enum isn't auto-Equatable
        // — match on the cases instead of comparing them.
        var seen: Set<String> = []
        for mode in [ClaudeLauncher.DesktopMode.chat, .cowork,
                     .code(folder: URL(fileURLWithPath: "/tmp"))] {
            switch mode {
            case .chat:   seen.insert("chat")
            case .cowork: seen.insert("cowork")
            case .code:   seen.insert("code")
            }
        }
        #expect(seen == ["chat", "cowork", "code"])
    }
}
