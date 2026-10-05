# Configurable Claude Launch (#12) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users configure how "Launch Claude" opens a Claude Code CLI session — a custom `claude` binary path and a terminal target (Auto / Terminal.app / iTerm2 / Custom command).

**Architecture:** `ClaudeLauncher` stays a stateless enum; a `CLIConfig` value is injected from the call site (built from `@AppStorage`) so the terminal/command logic is pure and unit-testable. Named terminal targets use AppleScript/`NSWorkspace` (macOS-only); the custom-command template (`{cwd}`/`{claude}` → shell) is the platform-agnostic path. Pure string builders (path resolution, command expansion, AppleScript generation) are tested; the thin `NSWorkspace`/`NSAppleScript` invocation is not.

**Tech Stack:** Swift, SwiftUI, AppKit (`NSWorkspace`, `NSAppleScript`), Swift Testing (`import Testing`), `@AppStorage`. Build/test via `xcodebuild`.

**Design doc:** `docs/launch-claude-customization-design.md`

**Conventions for this codebase:**
- New `.swift` files under `Scout/` and `ScoutTests/` are auto-discovered (synchronized file groups) — no `.xcodeproj` edits needed.
- `import Testing` / `@testable import Scout` may show as SourceKit "No such module" / "Cannot find type" false positives in-editor — ignore; trust `xcodebuild`.
- Run a single suite with the **type name**, never a directory: `-only-testing:ScoutTests/CLILauncherTests` works; `-only-testing:ScoutTests/ActionItems` runs ZERO tests (false green).
- Test files that use Combine/etc. need explicit `import` (the project enables `MemberImportVisibility`).

**Single-suite test command (used throughout):**
```bash
xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' \
  -only-testing:ScoutTests/CLILauncherTests \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" -quiet
```

**Full-suite test command (Task 8):**
```bash
xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' \
  -only-testing:ScoutTests \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" -quiet
```

---

## File Structure

- **Create** `Scout/Utilities/CLITerminal.swift` — `CLITerminal` enum + `CLIConfig` struct (the injected config model).
- **Modify** `Scout/Utilities/ClaudeLauncher.swift` — replace `Target.ghostty` with `Target.cli`; add `resolveClaudePath(override:)`, pure builders (`shellQuote`, `shellDoubleQuoteEscape`, `appleScriptEscape`, `expandCustomCommand`, `makeTerminalShellCommand`, `makeTerminalAppScript`, `makeITermScript`); add dispatch (`launchCLI`, `launchAuto`, `launchTerminalApp`, `launchITerm`, `launchCustom`, `runAppleScript`); add `LaunchError` cases.
- **Modify** `Scout/Shell/SettingsView.swift` — add a "Claude Code" settings card (3 `@AppStorage` keys + a terminal picker).
- **Modify** `Scout/ActionItems/Views/TaskActionsView.swift` — read the 3 keys, build `CLIConfig`, update the menu label, call `.cli`.
- **Create** `ScoutTests/ActionItems/CLILauncherTests.swift` — `@Suite("CLILauncher")` covering the pure builders + model.

---

## Task 1: CLITerminal + CLIConfig model

**Files:**
- Create: `Scout/Utilities/CLITerminal.swift`
- Test: `ScoutTests/ActionItems/CLILauncherTests.swift`

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/ActionItems/CLILauncherTests.swift`:

```swift
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
```

- [ ] **Step 2: Run test to verify it fails**

Run the single-suite command above.
Expected: FAIL — compile error, `Cannot find 'CLITerminal' in scope`.

- [ ] **Step 3: Write minimal implementation**

Create `Scout/Utilities/CLITerminal.swift`:

```swift
import Foundation

/// Which terminal Scout uses to open an interactive Claude Code CLI session
/// from an action item. Named cases are macOS-specific; `.custom` is the
/// platform-agnostic escape hatch (a user-supplied command template) and the
/// seam a future Linux/Windows port would extend.
enum CLITerminal: String, CaseIterable, Identifiable {
    case auto
    case terminalApp
    case iterm2
    case custom

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .auto:        return "Auto"
        case .terminalApp: return "Terminal.app"
        case .iterm2:      return "iTerm2"
        case .custom:      return "Custom command"
        }
    }
}

/// Injected into `ClaudeLauncher` so the launch logic stays a pure, testable
/// function of its inputs rather than reading `UserDefaults` directly.
struct CLIConfig: Equatable {
    var claudePathOverride: String   // "" = no override
    var terminal: CLITerminal
    var customCommand: String        // "" = none; used only when terminal == .custom

    static let auto = CLIConfig(claudePathOverride: "", terminal: .auto, customCommand: "")
}
```

- [ ] **Step 4: Run test to verify it passes**

Run the single-suite command. Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add Scout/Utilities/CLITerminal.swift ScoutTests/ActionItems/CLILauncherTests.swift
git commit -m "feat(launch): add CLITerminal + CLIConfig model (#12)"
```

---

## Task 2: claude path override resolution

**Files:**
- Modify: `Scout/Utilities/ClaudeLauncher.swift:154` (`resolveClaudePath`)
- Test: `ScoutTests/ActionItems/CLILauncherTests.swift`

- [ ] **Step 1: Write the failing test**

Add to `CLILauncherTests`:

```swift
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
```

- [ ] **Step 2: Run test to verify it fails**

Run the single-suite command.
Expected: FAIL — compile error, `resolveClaudePath` is `private` and has no `override:` parameter.

- [ ] **Step 3: Write minimal implementation**

In `Scout/Utilities/ClaudeLauncher.swift`, replace the existing `private static func resolveClaudePath() -> String?` (line ~154) with:

```swift
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
```

Note: the only behavioral change vs. the original is the `override:` branch + dropping `private`. The probe/login-shell logic is unchanged. The existing caller at line ~117 (`guard let claudePath = resolveClaudePath()`) will be replaced in Task 5; it will not compile until then — that's expected, Task 2 verifies via the test compile of the new signature. If the build blocks the test, temporarily update line ~117 to `resolveClaudePath(override: "")` (Task 5 restructures it anyway).

- [ ] **Step 4: Run test to verify it passes**

Run the single-suite command. Expected: PASS (the 2 new tests). If the launcher fails to compile due to the old call site, make the one-line edit noted above, then re-run.

- [ ] **Step 5: Commit**

```bash
git add Scout/Utilities/ClaudeLauncher.swift ScoutTests/ActionItems/CLILauncherTests.swift
git commit -m "feat(launch): claude path override in resolveClaudePath (#12)"
```

---

## Task 3: custom-command template expansion

**Files:**
- Modify: `Scout/Utilities/ClaudeLauncher.swift` (add `shellQuote`, `expandCustomCommand`)
- Test: `ScoutTests/ActionItems/CLILauncherTests.swift`

- [ ] **Step 1: Write the failing test**

Add to `CLILauncherTests`:

```swift
    // MARK: - custom command expansion

    @Test func shellQuote_wrapsAndEscapesSingleQuotes() {
        #expect(ClaudeLauncher.shellQuote("/usr/bin/claude") == "'/usr/bin/claude'")
        #expect(ClaudeLauncher.shellQuote("/a b/claude") == "'/a b/claude'")
        // Embedded single quote: ' -> '\'' (close, escaped quote, reopen)
        #expect(ClaudeLauncher.shellQuote("a'b") == "'a'\\''b'")
    }

    @Test func expandCustomCommand_substitutesQuotedPlaceholders() {
        let out = ClaudeLauncher.expandCustomCommand(
            template: "kitty -d {cwd} -e {claude}",
            claude: "/opt/claude",
            cwd: "/Users/me/Scout")
        #expect(out == "kitty -d '/Users/me/Scout' -e '/opt/claude'")
    }

    @Test func expandCustomCommand_handlesSpacesAndRepeats() {
        let out = ClaudeLauncher.expandCustomCommand(
            template: "{claude} --cwd {cwd} ; echo {cwd}",
            claude: "/a b/claude",
            cwd: "/c d")
        #expect(out == "'/a b/claude' --cwd '/c d' ; echo '/c d'")
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run the single-suite command.
Expected: FAIL — `Cannot find 'shellQuote'` / `'expandCustomCommand'`.

- [ ] **Step 3: Write minimal implementation**

In `ClaudeLauncher.swift`, add inside the enum (e.g. after `resolveClaudePath`):

```swift
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
    static func expandCustomCommand(template: String, claude: String, cwd: String) -> String {
        template
            .replacingOccurrences(of: "{claude}", with: shellQuote(claude))
            .replacingOccurrences(of: "{cwd}", with: shellQuote(cwd))
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run the single-suite command. Expected: PASS (3 new tests).

- [ ] **Step 5: Commit**

```bash
git add Scout/Utilities/ClaudeLauncher.swift ScoutTests/ActionItems/CLILauncherTests.swift
git commit -m "feat(launch): custom-command template expansion (#12)"
```

---

## Task 4: terminal shell command + AppleScript builders

**Files:**
- Modify: `Scout/Utilities/ClaudeLauncher.swift` (add `shellDoubleQuoteEscape`, `appleScriptEscape`, `makeTerminalShellCommand`, `makeTerminalAppScript`, `makeITermScript`)
- Test: `ScoutTests/ActionItems/CLILauncherTests.swift`

- [ ] **Step 1: Write the failing test**

Add to `CLILauncherTests`:

```swift
    // MARK: - terminal + AppleScript builders

    @Test func makeTerminalShellCommand_basic() {
        let cmd = ClaudeLauncher.makeTerminalShellCommand(claude: "/cl", cwd: "/w")
        #expect(cmd.hasPrefix("cd \"/w\" && "))
        #expect(cmd.hasSuffix("exec \"/cl\""))
        #expect(cmd.contains("clear"))
    }

    @Test func makeTerminalShellCommand_escapesQuotesAndBackslashes() {
        // cwd contains a quote and a backslash; both must be escaped for the
        // surrounding double-quoted shell string.
        let cmd = ClaudeLauncher.makeTerminalShellCommand(claude: "/cl", cwd: #"/a"b\c"#)
        #expect(cmd.contains(#"cd "/a\"b\\c""#))
    }

    @Test func makeTerminalAppScript_wrapsInTellBlock() {
        let s = ClaudeLauncher.makeTerminalAppScript(claude: "/cl", cwd: "/w")
        #expect(s.contains(#"tell application "Terminal""#))
        #expect(s.contains("activate"))
        #expect(s.contains("do script"))
        // The shell command's double-quotes are AppleScript-escaped (\").
        #expect(s.contains(#"cd \"/w\""#))
    }

    @Test func makeITermScript_usesDefaultProfileAndWriteText() {
        let s = ClaudeLauncher.makeITermScript(claude: "/cl", cwd: "/w")
        #expect(s.contains(#"tell application "iTerm""#))
        #expect(s.contains("create window with default profile"))
        #expect(s.contains("write text"))
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run the single-suite command.
Expected: FAIL — `Cannot find 'makeTerminalShellCommand'` etc.

- [ ] **Step 3: Write minimal implementation**

In `ClaudeLauncher.swift`, add to the command-builders section:

```swift
    /// Escape a string for embedding inside a double-quoted shell token:
    /// backslash first, then the double quote.
    static func shellDoubleQuoteEscape(_ s: String) -> String {
        s.replacingOccurrences(of: "\\", with: "\\\\")
         .replacingOccurrences(of: "\"", with: "\\\"")
    }

    /// Escape a string for embedding inside an AppleScript string literal.
    static func appleScriptEscape(_ s: String) -> String {
        s.replacingOccurrences(of: "\\", with: "\\\\")
         .replacingOccurrences(of: "\"", with: "\\\"")
    }

    /// The shell command run inside Terminal.app / iTerm2: cd to the working
    /// dir, print the clipboard-paste hint, then exec claude. Mirrors
    /// `makeGhosttyScript`'s double-quote escaping.
    static func makeTerminalShellCommand(claude: String, cwd: String) -> String {
        let cwdEsc = shellDoubleQuoteEscape(cwd)
        let claudeEsc = shellDoubleQuoteEscape(claude)
        return "cd \"\(cwdEsc)\" && clear && "
            + "echo 'Scout: action-item context copied to your clipboard. Paste with Cmd+V.' && "
            + "exec \"\(claudeEsc)\""
    }

    static func makeTerminalAppScript(claude: String, cwd: String) -> String {
        let cmd = appleScriptEscape(makeTerminalShellCommand(claude: claude, cwd: cwd))
        return """
        tell application "Terminal"
          activate
          do script "\(cmd)"
        end tell
        """
    }

    static func makeITermScript(claude: String, cwd: String) -> String {
        let cmd = appleScriptEscape(makeTerminalShellCommand(claude: claude, cwd: cwd))
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
```

- [ ] **Step 4: Run test to verify it passes**

Run the single-suite command. Expected: PASS (4 new tests).

- [ ] **Step 5: Commit**

```bash
git add Scout/Utilities/ClaudeLauncher.swift ScoutTests/ActionItems/CLILauncherTests.swift
git commit -m "feat(launch): terminal shell + AppleScript builders (#12)"
```

---

## Task 5: wire up the launch dispatch

**Files:**
- Modify: `Scout/Utilities/ClaudeLauncher.swift` (Target case, LaunchError cases, dispatch funcs; remove old `launchGhostty`)

No new unit test — this is the integration glue around the already-tested pure builders, plus `NSWorkspace`/`NSAppleScript` calls that can't run headless. Verification is "the full suite still builds and passes" (Task 8) plus the manual smoke test (Task 8).

- [ ] **Step 1: Replace the `Target` enum case**

In `ClaudeLauncher.swift`, change:

```swift
    enum Target {
        case ghostty(cwd: URL)
        case claudeDesktop(DesktopMode)
    }
```
to:
```swift
    enum Target {
        case cli(cwd: URL, config: CLIConfig)
        case claudeDesktop(DesktopMode)
    }
```

- [ ] **Step 2: Add the new `LaunchError` cases**

In the `LaunchError` enum, add these cases alongside the existing ones:

```swift
        case iterm2NotInstalled
        case terminalLaunchFailed(String)
        case customCommandEmpty
```

And in `errorDescription`'s switch, add:

```swift
            case .iterm2NotInstalled:
                return "iTerm2 isn't installed. Install it from https://iterm2.com or pick a different terminal in Settings."
            case .terminalLaunchFailed(let msg):
                return "Couldn't open the terminal: \(msg). If this is a permissions error, grant Scout access under System Settings → Privacy & Security → Automation."
            case .customCommandEmpty:
                return "Custom command is selected but empty. Add a launch command in Settings (use {cwd} and {claude})."
```

- [ ] **Step 3: Update the `launch(target:)` switch**

Change the dispatch switch in `static func launch(target:prompt:)`:

```swift
        switch target {
        case .cli(let cwd, let config):  try launchCLI(cwd: cwd, config: config)
        case .claudeDesktop(let mode):   try launchClaudeDesktop(prompt: prompt, mode: mode)
        }
```

- [ ] **Step 4: Replace `launchGhostty` with the dispatch + `launchAuto`**

Delete the existing `private static func launchGhostty(cwd:)` (lines ~104–139) and add:

```swift
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
```

- [ ] **Step 5: Add the Terminal.app / iTerm2 / custom launch functions**

Add to `ClaudeLauncher` (near the other launch helpers):

```swift
    private static let itermBundleID = "com.googlecode.iterm2"

    private static func launchTerminalApp(claudePath: String, cwd: URL) throws {
        try runAppleScript(makeTerminalAppScript(claude: claudePath, cwd: cwd.path))
    }

    private static func launchITerm(claudePath: String, cwd: URL) throws {
        guard NSWorkspace.shared.urlForApplication(
            withBundleIdentifier: itermBundleID
        ) != nil else {
            throw LaunchError.iterm2NotInstalled
        }
        try runAppleScript(makeITermScript(claude: claudePath, cwd: cwd.path))
    }

    private static func launchCustom(claudePath: String, cwd: URL, command: String) throws {
        let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw LaunchError.customCommandEmpty }
        let expanded = expandCustomCommand(template: trimmed, claude: claudePath, cwd: cwd.path)
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

    /// Run an AppleScript source string on the main thread. Surfaces compile
    /// and execution errors (including Automation-permission denial) as
    /// `terminalLaunchFailed`.
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
```

- [ ] **Step 6: Build to verify the launcher compiles**

The call site in `TaskActionsView` still uses `.ghostty(...)` and will fail to compile — that is fixed in Task 7. To verify Task 5 in isolation, build the launcher's own correctness by running the single suite (it compiles `ClaudeLauncher` as part of the Scout module). If the app target fails only on `TaskActionsView`'s `.ghostty`, proceed to Task 6/7; do not "fix" it here.

Run:
```bash
xcodebuild build -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" -quiet 2>&1 | grep -E "error:" | head
```
Expected: the only errors reference `TaskActionsView.swift` and `.ghostty` (resolved in Task 7). If there are errors in `ClaudeLauncher.swift`, fix them before moving on.

- [ ] **Step 7: Commit**

```bash
git add Scout/Utilities/ClaudeLauncher.swift
git commit -m "feat(launch): dispatch to Auto/Terminal/iTerm2/custom targets (#12)"
```

---

## Task 6: Settings — "Claude Code" card

**Files:**
- Modify: `Scout/Shell/SettingsView.swift`

No unit test (SettingsView is UI and untested in this codebase). Verified by build (Task 8) and manual smoke (Task 8).

- [ ] **Step 1: Add the `@AppStorage` keys + detected-path state**

In `SettingsView`, alongside the existing `@AppStorage` declarations (top of the struct), add:

```swift
    @AppStorage("claudeCLIPath")       private var claudeCLIPath: String = ""
    @AppStorage("cliTerminal")         private var cliTerminal: String = CLITerminal.auto.rawValue
    @AppStorage("customLaunchCommand") private var customLaunchCommand: String = ""
    @State private var detectedClaudePath: String?
```

- [ ] **Step 2: Add the "Claude Code" section after "General"**

In `body`, immediately after the `section(label: "General") { … }` block and before `section(label: "Linear")`, insert:

```swift
                section(label: "Claude Code") {
                    SettingsCard {
                        SettingsField(
                            label: "Claude binary path",
                            help: "Absolute path to the `claude` CLI. Leave blank to auto-detect (`~/.local/bin`, Homebrew, then your login shell)."
                        ) {
                            SettingsInput(
                                text: $claudeCLIPath,
                                placeholder: detectedClaudePath ?? "Auto-detect")
                        }
                        SettingsRow(
                            title: "Open Claude Code in",
                            help: "Which terminal the Launch Claude → Claude Code option uses. Auto prefers Ghostty/tmux and falls back to Terminal.app."
                        ) {
                            Picker("", selection: $cliTerminal) {
                                ForEach(CLITerminal.allCases) { t in
                                    Text(t.displayName).tag(t.rawValue)
                                }
                            }
                            .labelsHidden()
                            .pickerStyle(.menu)
                            .fixedSize()
                        }
                        if cliTerminal == CLITerminal.custom.rawValue {
                            SettingsField(
                                label: "Custom launch command",
                                help: "Shell command run via your login shell. `{cwd}` and `{claude}` are inserted as quoted arguments. Example: `kitty -d {cwd} -e {claude}`."
                            ) {
                                SettingsInput(
                                    text: $customLaunchCommand,
                                    placeholder: "kitty -d {cwd} -e {claude}")
                            }
                        }
                    }
                }
```

- [ ] **Step 3: Populate the detected path once on appear**

Add a `.task` modifier to the `ScrollView` in `body` (after the existing `.frame(...)`/`.padding(...)` chain on the `VStack`, attach to the outer `ScrollView`):

```swift
        .task {
            // Resolve once off the main run loop; the login-shell probe can
            // take ~100ms and must not run on every render.
            let detected = await Task.detached {
                ClaudeLauncher.resolveClaudePath(override: "")
            }.value
            detectedClaudePath = detected
        }
```

- [ ] **Step 4: Build to verify**

```bash
xcodebuild build -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" -quiet 2>&1 | grep -E "error:" | head
```
Expected: errors only in `TaskActionsView.swift` re `.ghostty` (fixed next). No errors in `SettingsView.swift`.

- [ ] **Step 5: Commit**

```bash
git add Scout/Shell/SettingsView.swift
git commit -m "feat(settings): Claude Code card — path + terminal target (#12)"
```

---

## Task 7: TaskActionsView — build config + call `.cli`

**Files:**
- Modify: `Scout/ActionItems/Views/TaskActionsView.swift:56-96` (the menu) and `:59` (call site)

- [ ] **Step 1: Add the `@AppStorage` keys**

Near the top of `TaskActionsView` (with its other stored properties), add:

```swift
    @AppStorage("claudeCLIPath")       private var claudeCLIPath: String = ""
    @AppStorage("cliTerminal")         private var cliTerminal: String = CLITerminal.auto.rawValue
    @AppStorage("customLaunchCommand") private var customLaunchCommand: String = ""
```

(If `import SwiftUI` is already present it provides `@AppStorage`; no new import needed.)

- [ ] **Step 2: Replace the Ghostty menu button**

Change the first `Button { launch(.ghostty(cwd: scoutDirectory)) } label: { Label("Ghostty → tmux + Claude Code", systemImage: "terminal") }` to:

```swift
            Button {
                let config = CLIConfig(
                    claudePathOverride: claudeCLIPath,
                    terminal: CLITerminal(rawValue: cliTerminal) ?? .auto,
                    customCommand: customLaunchCommand)
                launch(.cli(cwd: scoutDirectory, config: config))
            } label: {
                Label(cliMenuLabel, systemImage: "terminal")
            }
```

- [ ] **Step 3: Add the `cliMenuLabel` computed property**

Add to `TaskActionsView` (near the `launch(_:)` helper):

```swift
    private var cliMenuLabel: String {
        switch CLITerminal(rawValue: cliTerminal) ?? .auto {
        case .auto:        return "Launch Claude Code (Auto)"
        case .terminalApp: return "Open in Terminal.app → Claude Code"
        case .iterm2:      return "Open in iTerm2 → Claude Code"
        case .custom:      return "Open in custom terminal → Claude Code"
        }
    }
```

- [ ] **Step 4: Build to verify the whole app compiles**

```bash
xcodebuild build -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" -quiet 2>&1 | grep -E "error:" | head
```
Expected: no `error:` lines (clean build).

- [ ] **Step 5: Commit**

```bash
git add Scout/ActionItems/Views/TaskActionsView.swift
git commit -m "feat(launch): drive Launch Claude from configured terminal target (#12)"
```

---

## Task 8: Full verification, manual smoke, PR

**Files:** none (verification + PR)

- [ ] **Step 1: Run the full test suite**

Run the full-suite command (top of plan).
Expected: TEST SUCCEEDED, 0 failures. Confirms the `CLILauncherTests` suite plus all existing suites pass and the `.ghostty`→`.cli` refactor didn't break anything.

- [ ] **Step 2: Manual smoke test (cannot be automated — real terminal launches)**

Launch the app (`open` the built `.app`, or run from Xcode). For an action item, open the **Launch Claude** menu and verify, switching the Settings → Claude Code → "Open Claude Code in" value between runs:
- **Auto** (Ghostty installed): opens Ghostty/tmux as before.
- **Terminal.app**: opens a Terminal window that `cd`s to `~/Scout` and runs `claude`.
- **iTerm2** (installed): opens an iTerm2 window running `claude` (approve the Automation prompt the first time).
- **Custom** with `kitty -d {cwd} -e {claude}` (if kitty installed) or another known terminal: launches it.
- **Custom** left blank: shows the "Custom command is selected but empty" error in the banner.
- **Claude binary path** set to a bogus path: shows the "Couldn't find the `claude` CLI" error.

Record the results in the PR description.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feat/12-configurable-claude-launch
gh pr create --title "feat(launch): configurable claude path & terminal target (#12)" \
  --base main --body "<summary + manual smoke results; Closes #12>"
```

- [ ] **Step 4: Confirm CI is green on the PR**

```bash
gh pr checks --watch
```
Expected: the `ScoutTests` workflow passes.

---

## Self-review notes (for the implementer)

- The pure builders (Tasks 2–4) are the tested correctness core. Tasks 5–7 are glue verified by build + the full suite + manual smoke.
- `resolveClaudePath` becomes non-`private` (internal) so `@testable` tests reach it; this is intentional.
- Escaping has two layers for AppleScript targets: shell double-quote escaping inside `makeTerminalShellCommand`, then `appleScriptEscape` over the whole command in `makeTerminalAppScript`/`makeITermScript`. The custom path uses single-quote `shellQuote` instead (no AppleScript layer). Don't mix them.
- `Target.cli` fully replaces `Target.ghostty`; the only call site is `TaskActionsView` (verified). If a future call site needs the old behavior, it passes `CLIConfig.auto`.
