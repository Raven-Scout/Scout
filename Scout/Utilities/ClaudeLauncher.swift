import Foundation
import AppKit

/// Launches an interactive Claude session seeded with the context of an
/// action item. Two targets are supported — a Claude Code CLI session
/// (target is configurable in Settings: Auto prefers Ghostty/tmux and falls
/// back to Terminal.app; Terminal.app, iTerm2, and a custom command are also
/// supported), or Claude Desktop's main chat.
///
/// The full action-item context is always copied to the clipboard so the
/// user can paste it with Cmd+V as a reliable fallback if the platform's
/// native prefill mechanism is flaky.
enum ClaudeLauncher {
    enum CopyFormat: String, CaseIterable, Identifiable {
        case fullContext
        case concise
        case markdownChecklist

        var id: String { rawValue }

        var label: String {
            switch self {
            case .fullContext:       return "Full context"
            case .concise:           return "Concise"
            case .markdownChecklist: return "Markdown checklist"
            }
        }

        var systemImage: String {
            switch self {
            case .fullContext:       return "doc.text"
            case .concise:           return "text.alignleft"
            case .markdownChecklist: return "checklist"
            }
        }
    }

    enum DesktopMode {
        /// `claude://claude.ai/new` — main chat. Reliably opens a fresh chat
        /// with the prompt prefilled from any screen.
        case chat
        /// `claude://cowork/new` — dispatches into Cowork's composer with
        /// ``prefillOnly: true``. Works only when the Cowork screen is
        /// currently mountable, and appends to any existing composer text
        /// rather than replacing it.
        case cowork
    }

    enum Target {
        case cli(cwd: URL, config: CLIConfig)
        case claudeDesktop(DesktopMode)
    }

    enum LaunchError: LocalizedError {
        case ghosttyNotInstalled  // reserved for a future explicit .ghostty target; not thrown by .auto
        case claudeDesktopNotInstalled
        case claudeCLINotFound
        case scriptWriteFailed(String)
        case urlBuildFailed
        case iterm2NotInstalled
        case terminalLaunchFailed(String)
        case customCommandEmpty

        var errorDescription: String? {
            switch self {
            case .ghosttyNotInstalled:
                return "Ghostty.app isn't installed. Install it from https://ghostty.org to use this option."
            case .claudeDesktopNotInstalled:
                return "Claude.app isn't installed. Download Claude Desktop from https://claude.ai/download to use this option."
            case .claudeCLINotFound:
                return "Couldn't find the `claude` CLI. Install it from https://claude.com/claude-code or run `which claude` from a terminal to confirm it's on your PATH."
            case .scriptWriteFailed(let msg):
                return "Couldn't prepare Claude launch helper: \(msg)"
            case .urlBuildFailed:
                return "Couldn't build claude:// URL."
            case .iterm2NotInstalled:
                return "iTerm2 isn't installed. Install it from https://iterm2.com or pick a different terminal in Settings."
            case .terminalLaunchFailed(let msg):
                return "Couldn't open the terminal: \(msg). If this is a permissions error, grant Scout access under System Settings → Privacy & Security → Automation."
            case .customCommandEmpty:
                return "Custom command is selected but empty. Add a launch command in Settings (use {cwd} and {claude})."
            }
        }
    }

    static func launch(target: Target, prompt: String) throws {
        // Clipboard is the universal fallback. Ghostty's tmux-based flow
        // needs it for ⌘V into the claude TUI; the Claude Desktop URL-prefill
        // sometimes drops the `q` param during screen transitions, and the
        // clipboard lets the user recover with ⌘A ⌘V.
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(prompt, forType: .string)

        switch target {
        case .cli(let cwd, let config):  try launchCLI(cwd: cwd, config: config)
        case .claudeDesktop(let mode):   try launchClaudeDesktop(prompt: prompt, mode: mode)
        }
    }

    /// Build the prompt text for a task — subject, plus body, recent
    /// comments, and any deep links.
    static func prompt(for task: ActionTask) -> String {
        prompt(for: task, format: .fullContext)
    }

    static func prompt(for task: ActionTask, format: CopyFormat) -> String {
        switch format {
        case .fullContext:
            return "Help me make progress on this action item:\n\n" + fullContextBody(for: task)
        case .concise:
            return conciseBody(for: task)
        case .markdownChecklist:
            return checklistBody(for: task)
        }
    }

    static func prompt(for tasks: [ActionTask], format: CopyFormat) -> String {
        guard let first = tasks.first else { return "" }
        guard tasks.count > 1 else { return prompt(for: first, format: format) }

        switch format {
        case .fullContext:
            let items = tasks.enumerated().map { index, task in
                "## \(index + 1). \(fullContextBody(for: task))"
            }
            return "Help me make progress on these \(tasks.count) action items:\n\n"
                + items.joined(separator: "\n\n---\n\n")
        case .concise:
            return tasks.enumerated()
                .map {
                    let body = conciseBody(for: $0.element)
                        .replacingOccurrences(of: "\n", with: "\n   ")
                    return "\($0.offset + 1). \(body)"
                }
                .joined(separator: "\n\n")
        case .markdownChecklist:
            return tasks.map { checklistBody(for: $0) }.joined(separator: "\n")
        }
    }

    private static func fullContextBody(for task: ActionTask) -> String {
        var out = task.plainSubject
        if !task.body.isEmpty {
            out += "\n\n\(task.body)"
        }
        if !task.comments.isEmpty {
            let block = task.comments
                .map { c in
                    let ts = c.timestamp.isEmpty ? "" : " (\(c.timestamp))"
                    return "- \(c.author)\(ts): \(c.text)"
                }
                .joined(separator: "\n")
            out += "\n\nPrior comments:\n\(block)"
        }
        if !task.deepLinks.isEmpty {
            let block = task.deepLinks
                .map { "- \($0.displayLabel): \($0.openURL.absoluteString)" }
                .joined(separator: "\n")
            out += "\n\nLinks:\n\(block)"
        }
        return out
    }

    private static func conciseBody(for task: ActionTask) -> String {
        guard !task.body.isEmpty else { return task.plainSubject }
        return "\(task.plainSubject)\n\(task.body)"
    }

    private static func checklistBody(for task: ActionTask) -> String {
        var lines = ["- [\(task.done ? "x" : " ")] \(task.plainSubject)"]
        if !task.body.isEmpty {
            lines.append(contentsOf: task.body.split(separator: "\n").map { "  \($0)" })
        }
        if !task.deepLinks.isEmpty {
            lines.append(contentsOf: task.deepLinks.map {
                "  - [\($0.displayLabel)](\($0.openURL.absoluteString))"
            })
        }
        return lines.joined(separator: "\n")
    }

    // MARK: - Command builders (pure, unit-tested)

    /// POSIX single-quote a string so it survives as one shell argument,
    /// regardless of spaces or metacharacters. Embedded single quotes are
    /// closed, backslash-escaped, and reopened (`'\''`).
    static func shellQuote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    /// Expand a custom launch-command template. `{claude}` and `{cwd}` are
    /// replaced with shell-quoted values, so the user writes them unquoted:
    /// e.g. `kitty -d {cwd} -e {claude}`.
    static func expandCustomCommand(template: String, claudePath: String, cwd: String) -> String {
        template
            .replacingOccurrences(of: "{claude}", with: shellQuote(claudePath))
            .replacingOccurrences(of: "{cwd}", with: shellQuote(cwd))
    }

    /// Escape a string for embedding inside a double-quoted shell token:
    /// backslash first, then the double quote.
    static func shellDoubleQuoteEscape(_ s: String) -> String {
        s.replacingOccurrences(of: "\\", with: "\\\\")
         .replacingOccurrences(of: "\"", with: "\\\"")
    }

    /// Escape a string so it can be embedded inside an AppleScript string
    /// literal in NSAppleScript source. Only `\` and `"` need escaping; the
    /// sequences `\\` and `\"` are honoured by NSAppleScript/osascript on
    /// macOS, even though they aren't in the AppleScript Language Guide. To
    /// embed a literal newline/tab, use AppleScript string concatenation
    /// instead — those escapes are not defined for AppleScript strings.
    static func appleScriptEscape(_ s: String) -> String {
        s.replacingOccurrences(of: "\\", with: "\\\\")
         .replacingOccurrences(of: "\"", with: "\\\"")
    }

    /// The shell command run inside Terminal.app / iTerm2: cd to the working
    /// dir, print the clipboard-paste hint, then exec claude. Mirrors
    /// `makeGhosttyScript`'s double-quote escaping.
    static func makeTerminalShellCommand(claudePath: String, cwd: String) -> String {
        let cwdEsc = shellDoubleQuoteEscape(cwd)
        let claudeEsc = shellDoubleQuoteEscape(claudePath)
        return "cd \"\(cwdEsc)\" && clear && "
            + "echo 'Scout: action-item context copied to your clipboard. Paste with Cmd+V.' && "
            + "exec \"\(claudeEsc)\""
    }

    /// Build the AppleScript that opens a new Terminal.app window running the
    /// claude session. The returned string is ready to pass to
    /// `NSAppleScript(source:)` — already fully escaped, do not re-escape.
    static func makeTerminalAppScript(claudePath: String, cwd: String) -> String {
        let cmd = appleScriptEscape(makeTerminalShellCommand(claudePath: claudePath, cwd: cwd))
        return """
        tell application "Terminal"
          activate
          do script "\(cmd)"
        end tell
        """
    }

    /// Build the AppleScript that opens a new iTerm2 window running the claude
    /// session. The returned string is ready to pass to `NSAppleScript(source:)`
    /// — already fully escaped, do not re-escape.
    static func makeITermScript(claudePath: String, cwd: String) -> String {
        let cmd = appleScriptEscape(makeTerminalShellCommand(claudePath: claudePath, cwd: cwd))
        return """
        tell application "iTerm"
          activate
          set newWindow to (create window with default profile)
          tell current session of newWindow
            write text "\(cmd)"
          end tell
        end tell
        """
    }

    // MARK: - Ghostty

    private static let ghosttyBundleID = "com.mitchellh.ghostty"

    /// Common Homebrew + system paths. Scout.app is launched by macOS with
    /// a minimal PATH, so we probe absolute locations instead of relying on
    /// `which`.
    private static let tmuxPaths = [
        "/opt/homebrew/bin/tmux",
        "/usr/local/bin/tmux",
        "/usr/bin/tmux",
    ]

    private static func launchCLI(cwd: URL, config: CLIConfig) throws {
        guard let claudePath = resolveClaudePath(override: config.claudePathOverride) else {
            throw LaunchError.claudeCLINotFound
        }
        switch config.terminal {
        case .auto:        try launchAuto(claudePath: claudePath, cwd: cwd)
        case .terminalApp: try launchTerminalApp(claudePath: claudePath, cwd: cwd)
        case .iterm2:      try launchITerm(claudePath: claudePath, cwd: cwd)
        case .custom:      try launchCustom(claudePath: claudePath, cwd: cwd, command: config.customCommand)
        }
    }

    /// Auto: Ghostty+tmux → fresh Ghostty window → Terminal.app fallback, so
    /// it works whether or not the user runs Ghostty.
    private static func launchAuto(claudePath: String, cwd: URL) throws {
        if let ghosttyURL = NSWorkspace.shared.urlForApplication(
            withBundleIdentifier: ghosttyBundleID
        ) {
            if launchViaTmux(claudePath: claudePath, cwd: cwd) {
                activateGhostty(ghosttyURL: ghosttyURL)
                return
            }
            try launchFreshGhosttyWindow(ghosttyURL: ghosttyURL, claudePath: claudePath, cwd: cwd)
            return
        }
        try launchTerminalApp(claudePath: claudePath, cwd: cwd)
    }

    /// Common install locations for the `claude` CLI. Probed in order so
    /// Anthropic's `~/.local/bin` installer default wins over a Homebrew
    /// path that might be stale.
    private static let claudePaths: [String] = [
        (NSString(string: "~/.local/bin/claude") as NSString).expandingTildeInPath,
        "/opt/homebrew/bin/claude",
        "/usr/local/bin/claude",
    ]

    /// Resolve `claude` to an absolute path, or nil if it can't be found.
    ///
    /// - If `override` is non-empty, it is authoritative: returned only if it
    ///   is an executable file, otherwise nil (so a typo'd override surfaces a
    ///   "not found" error instead of being masked by the probe fallback).
    /// - If `override` is empty, probe well-known locations, then ask the
    ///   user's login shell (picks up mise/asdf/nvm-style installs).
    static func resolveClaudePath(override: String) -> String? {
        let trimmed = override.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            return FileManager.default.isExecutableFile(atPath: trimmed) ? trimmed : nil
        }
        if let direct = claudePaths.first(where: {
            FileManager.default.isExecutableFile(atPath: $0)
        }) {
            return direct
        }
        let shellPath = ProcessInfo.processInfo.environment["SHELL"] ?? "/bin/zsh"
        let task = Process()
        task.executableURL = URL(fileURLWithPath: shellPath)
        // -l + -c sources the login init files (.zprofile / .bash_profile)
        // so PATH from the user's shell setup is available for `command -v`.
        task.arguments = ["-lc", "command -v claude"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        do {
            try task.run()
            task.waitUntilExit()
        } catch {
            return nil
        }
        guard task.terminationStatus == 0 else { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let resolved = (String(data: data, encoding: .utf8) ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return resolved.isEmpty ? nil : resolved
    }

    /// Returns true if a tmux session was found and a `claude` window was
    /// successfully spawned in it.
    private static func launchViaTmux(claudePath: String, cwd: URL) -> Bool {
        guard let tmuxPath = tmuxPaths.first(where: {
            FileManager.default.isExecutableFile(atPath: $0)
        }) else { return false }

        guard let session = firstTmuxSession(tmuxPath: tmuxPath) else {
            return false
        }

        let task = Process()
        task.executableURL = URL(fileURLWithPath: tmuxPath)
        // Point tmux at the user's default socket dir explicitly. GUI apps
        // inherit `TMPDIR=/var/folders/…`, while tmux stores its socket at
        // `/tmp/tmux-$UID/default` under the shell convention.
        task.environment = tmuxEnvironment()
        task.arguments = [
            "new-window",
            "-t", "\(session):",
            "-c", cwd.path,
            "-n", "claude",
            claudePath,
        ]
        task.standardOutput = Pipe()
        task.standardError = Pipe()
        do {
            try task.run()
            task.waitUntilExit()
        } catch {
            return false
        }
        return task.terminationStatus == 0
    }

    /// Lists tmux sessions and returns the first attached one (or the first
    /// session overall if none are attached).
    private static func firstTmuxSession(tmuxPath: String) -> String? {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: tmuxPath)
        task.environment = tmuxEnvironment()
        // "0 name" for attached, "1 name" for detached — sort puts
        // attached sessions first.
        task.arguments = [
            "list-sessions",
            "-F", "#{?session_attached,0,1} #{session_name}",
        ]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        do {
            try task.run()
            task.waitUntilExit()
        } catch {
            return nil
        }
        if task.terminationStatus != 0 { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let output = String(data: data, encoding: .utf8) ?? ""
        let sorted = output
            .split(whereSeparator: \.isNewline)
            .map(String.init)
            .sorted()
        guard let first = sorted.first else { return nil }
        let parts = first.split(separator: " ", maxSplits: 1)
        guard parts.count == 2 else { return nil }
        return String(parts[1])
    }

    private static func tmuxEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["TMPDIR"] = "/tmp"
        return env
    }

    private static func activateGhostty(ghosttyURL: URL) {
        let config = NSWorkspace.OpenConfiguration()
        config.activates = true
        config.createsNewApplicationInstance = false
        NSWorkspace.shared.openApplication(at: ghosttyURL, configuration: config) { _, error in
            if let error {
                NSLog("ClaudeLauncher: activate(Ghostty) failed: \(error.localizedDescription)")
            }
        }
    }

    private static func launchFreshGhosttyWindow(
        ghosttyURL: URL,
        claudePath: String,
        cwd: URL
    ) throws {
        let scriptURL = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("scout-launch-claude-\(UUID().uuidString).sh")
        do {
            try makeGhosttyScript(claudePath: claudePath, cwd: cwd)
                .write(to: scriptURL, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes(
                [.posixPermissions: NSNumber(value: 0o755)],
                ofItemAtPath: scriptURL.path
            )
        } catch {
            throw LaunchError.scriptWriteFailed(error.localizedDescription)
        }

        // `--command=` is a Ghostty CLI config override — it wins over the
        // user's `command = …` line in ~/.config/ghostty/config for this
        // instance. `createsNewApplicationInstance = true` is required on
        // macOS so our args aren't silently dropped by app activation.
        let config = NSWorkspace.OpenConfiguration()
        config.activates = true
        config.createsNewApplicationInstance = true
        config.arguments = ["--command=\(scriptURL.path)"]

        NSWorkspace.shared.openApplication(at: ghosttyURL, configuration: config) { _, error in
            if let error {
                NSLog("ClaudeLauncher: openApplication(Ghostty) failed: \(error.localizedDescription)")
            }
        }
    }

    private static func makeGhosttyScript(claudePath: String, cwd: URL) -> String {
        let cwdEsc = cwd.path
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
        let claudeEsc = claudePath
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
        // Ghostty inherits Scout's minimal launchd PATH, so we exec `claude`
        // by absolute path rather than relying on PATH lookup.
        return """
        #!/bin/bash
        cd "\(cwdEsc)" || exit 1
        clear
        echo "Scout: action-item context copied to your clipboard."
        echo "When Claude prompts you, paste (Cmd+V) and press Enter to send."
        echo
        exec "\(claudeEsc)"
        """
    }

    // MARK: - Terminal.app / iTerm2 / Custom

    private static let itermBundleID = "com.googlecode.iterm2"

    private static func launchTerminalApp(claudePath: String, cwd: URL) throws {
        try runAppleScript(makeTerminalAppScript(claudePath: claudePath, cwd: cwd.path))
    }

    private static func launchITerm(claudePath: String, cwd: URL) throws {
        guard NSWorkspace.shared.urlForApplication(
            withBundleIdentifier: itermBundleID
        ) != nil else {
            throw LaunchError.iterm2NotInstalled
        }
        try runAppleScript(makeITermScript(claudePath: claudePath, cwd: cwd.path))
    }

    private static func launchCustom(claudePath: String, cwd: URL, command: String) throws {
        let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw LaunchError.customCommandEmpty }
        let expanded = expandCustomCommand(template: trimmed, claudePath: claudePath, cwd: cwd.path)
        let shell = ProcessInfo.processInfo.environment["SHELL"] ?? "/bin/zsh"
        let task = Process()
        task.executableURL = URL(fileURLWithPath: shell)
        // Login shell so PATH-dependent commands (the user's terminal binary)
        // resolve. Launch-and-forget: the command spawns a GUI terminal and we
        // don't block the UI waiting on it.
        task.arguments = ["-lc", expanded]
        task.standardOutput = Pipe()
        task.standardError = Pipe()
        do {
            try task.run()
        } catch {
            throw LaunchError.terminalLaunchFailed(error.localizedDescription)
        }
    }

    /// Run an AppleScript source string. Surfaces compile and execution errors
    /// (including Automation-permission denial) as `terminalLaunchFailed`.
    private static func runAppleScript(_ source: String) throws {
        guard let script = NSAppleScript(source: source) else {
            throw LaunchError.terminalLaunchFailed("Could not compile the launch AppleScript.")
        }
        var errorInfo: NSDictionary?
        script.executeAndReturnError(&errorInfo)
        if let errorInfo {
            let msg = (errorInfo[NSAppleScript.errorMessage] as? String) ?? "unknown AppleScript error"
            throw LaunchError.terminalLaunchFailed(msg)
        }
    }

    // MARK: - Claude Desktop

    private static let claudeDesktopBundleID = "com.anthropic.claudefordesktop"

    private static func launchClaudeDesktop(prompt: String, mode: DesktopMode) throws {
        guard NSWorkspace.shared.urlForApplication(
            withBundleIdentifier: claudeDesktopBundleID
        ) != nil else {
            throw LaunchError.claudeDesktopNotInstalled
        }

        var components = URLComponents()
        components.scheme = "claude"
        switch mode {
        case .chat:
            components.host = "claude.ai"
            components.path = "/new"
        case .cowork:
            components.host = "cowork"
            components.path = "/new"
        }
        components.queryItems = [URLQueryItem(name: "q", value: prompt)]

        guard let url = components.url else {
            throw LaunchError.urlBuildFailed
        }
        NSWorkspace.shared.open(url)
    }
}
