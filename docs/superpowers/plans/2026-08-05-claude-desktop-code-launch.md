# Claude Desktop → Claude Code Launch Option (Default) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "new Claude Code session in Claude Desktop" launch target (`claude://code/new?q=…&folder=…`) to the action-item Launch Claude control, and make it the one-click default.

**Architecture:** `ClaudeLauncher.DesktopMode` gains a `.code(folder: URL)` case and a pure `makeDesktopURL(prompt:mode:)` builder (unit-tested); `TaskActionsView` swaps its plain `Menu` for a split control — a primary button firing the new default plus the existing chevron dropdown, reordered.

**Tech Stack:** Swift, SwiftUI, Swift Testing (`@Test`/`@Suite`), xcodebuild.

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-08-05-claude-desktop-code-launch-design.md`.
- Work on branch `claude/action-items-claude-code-c462c0` (already checked out with the spec committed).
- Build/test: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/<SuiteTypeName>`. As of 2026-08, `xcode-select -p` already points at Xcode.app, so the `DEVELOPER_DIR` prefix is belt-and-braces — keep it so the commands survive a reverted xcode-select.
- `-only-testing:` must name a real `@Suite` **type** (e.g. `ScoutTests/ClaudeDesktopURLTests`); a directory-style selector like `ScoutTests/ActionItems` silently runs ZERO tests and reports success.
- New `.swift` files under `Scout/` or `ScoutTests/` are picked up automatically (synchronized file groups) — do NOT edit `project.pbxproj`.
- SourceKit / IDE may show "Cannot find type … in scope" or "No such module 'Testing'" — false positives; `xcodebuild` is authoritative.
- No fixture or parser changes anywhere in this plan, so the three-repo `parser-corpus.json` sync rules in `CLAUDE.md` do not apply.
- Conventional-commit messages ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: `DesktopMode.code` + pure `makeDesktopURL` builder

**Files:**
- Modify: `Scout/Utilities/ClaudeLauncher.swift` (enum `DesktopMode` at lines 14–23; header comment at lines 4–12; `launchClaudeDesktop` at lines 461–484)
- Test: `ScoutTests/ActionItems/ClaudeDesktopURLTests.swift` (create)

**Interfaces:**
- Produces: `ClaudeLauncher.DesktopMode.code(folder: URL)` — new enum case.
- Produces: `static func makeDesktopURL(prompt: String, mode: DesktopMode) -> URL?` on `ClaudeLauncher` — pure builder consumed by `launchClaudeDesktop` and by Task 2's UI (indirectly via `Target.claudeDesktop`).

- [x] **Step 1: Write the failing tests**

Create `ScoutTests/ActionItems/ClaudeDesktopURLTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("ClaudeLauncher desktop URLs")
struct ClaudeDesktopURLTests {

    private func queryItems(_ url: URL) -> [String: String] {
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems ?? []
        return Dictionary(uniqueKeysWithValues: items.map { ($0.name, $0.value ?? "") })
    }

    @Test func chatURL_hostAndPromptQuery() throws {
        let url = try #require(ClaudeLauncher.makeDesktopURL(
            prompt: "hello", mode: .chat))
        #expect(url.scheme == "claude")
        #expect(url.host == "claude.ai")
        #expect(url.path == "/new")
        #expect(queryItems(url) == ["q": "hello"])
    }

    @Test func coworkURL_hostAndPromptQuery() throws {
        let url = try #require(ClaudeLauncher.makeDesktopURL(
            prompt: "hello", mode: .cowork))
        #expect(url.scheme == "claude")
        #expect(url.host == "cowork")
        #expect(url.path == "/new")
        #expect(queryItems(url) == ["q": "hello"])
    }

    @Test func codeURL_hostPromptAndFolderQuery() throws {
        let folder = URL(fileURLWithPath: "/Users/me/Scout")
        let url = try #require(ClaudeLauncher.makeDesktopURL(
            prompt: "fix the bug", mode: .code(folder: folder)))
        #expect(url.scheme == "claude")
        #expect(url.host == "code")
        #expect(url.path == "/new")
        #expect(queryItems(url) == [
            "q": "fix the bug",
            "folder": "/Users/me/Scout",
        ])
    }

    @Test func codeURL_promptWithMetacharactersRoundTrips() throws {
        // &, #, ?, =, newlines and spaces must survive URLComponents
        // percent-encoding and decode back to the original prompt.
        let prompt = "line one\nA & B? #tag = 100% done"
        let folder = URL(fileURLWithPath: "/tmp/with space/vault")
        let url = try #require(ClaudeLauncher.makeDesktopURL(
            prompt: prompt, mode: .code(folder: folder)))
        let items = queryItems(url)
        #expect(items["q"] == prompt)
        #expect(items["folder"] == "/tmp/with space/vault")
    }
}
```

- [x] **Step 2: Run tests to verify they fail**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ClaudeDesktopURLTests 2>&1 | grep -iE "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: FAIL — compile errors: `makeDesktopURL` is not a member of `ClaudeLauncher`, and `.code(folder:)` is not a member of `DesktopMode`.

- [x] **Step 3: Implement the enum case and builder**

In `Scout/Utilities/ClaudeLauncher.swift`:

3a. Add the case to `DesktopMode` (after `case cowork`, line 22):

```swift
        /// `claude://code/new` — new Claude Code session in the Code tab,
        /// prefilled with the prompt. `folder` becomes the session's working
        /// directory; Claude Desktop shows a one-time trust confirmation for
        /// folders adopted via deep link.
        case code(folder: URL)
```

3b. Replace the body of `launchClaudeDesktop` (lines 461–484) with a version
that delegates URL construction to the new pure builder, and add the builder:

```swift
    private static func launchClaudeDesktop(prompt: String, mode: DesktopMode) throws {
        guard NSWorkspace.shared.urlForApplication(
            withBundleIdentifier: claudeDesktopBundleID
        ) != nil else {
            throw LaunchError.claudeDesktopNotInstalled
        }
        guard let url = makeDesktopURL(prompt: prompt, mode: mode) else {
            throw LaunchError.urlBuildFailed
        }
        NSWorkspace.shared.open(url)
    }

    /// Build the claude:// deep link for a desktop mode. Pure and unit-tested;
    /// URLComponents handles percent-encoding of the prompt and folder path.
    /// Routes documented at support.claude.com article 14729294.
    static func makeDesktopURL(prompt: String, mode: DesktopMode) -> URL? {
        var components = URLComponents()
        components.scheme = "claude"
        components.path = "/new"
        components.queryItems = [URLQueryItem(name: "q", value: prompt)]
        switch mode {
        case .chat:
            components.host = "claude.ai"
        case .cowork:
            components.host = "cowork"
        case .code(let folder):
            components.host = "code"
            components.queryItems?.append(
                URLQueryItem(name: "folder", value: folder.path))
        }
        return components.url
    }
```

3c. Update the file-header doc comment (lines 4–12) so the target list stays
accurate — replace the sentence beginning "Two targets are supported" so the
paragraph reads:

```swift
/// Launches an interactive Claude session seeded with the context of an
/// action item. Two target families are supported — a Claude Code CLI session
/// (target is configurable in Settings: Auto prefers Ghostty/tmux and falls
/// back to Terminal.app; Terminal.app, iTerm2, and a custom command are also
/// supported), or Claude Desktop (a new Claude Code session in the Code tab,
/// the main chat, or a Cowork task).
```

- [x] **Step 4: Run tests to verify they pass**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ClaudeDesktopURLTests 2>&1 | grep -iE "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: PASS (4 tests).

- [x] **Step 5: Run the neighboring launcher suites to catch regressions**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/CLILauncherTests -only-testing:ScoutTests/ClaudeLauncherPromptTests 2>&1 | grep -iE "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add Scout/Utilities/ClaudeLauncher.swift ScoutTests/ActionItems/ClaudeDesktopURLTests.swift
git commit -m "feat(launcher): add Claude Desktop Code-tab deep link (claude://code/new)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Split launch control — one-click default + chevron menu

**Files:**
- Modify: `Scout/ActionItems/Views/TaskActionsView.swift` (`launchClaudeMenu`, lines 58–105; call site at line 47 stays as-is)

**Interfaces:**
- Consumes: `ClaudeLauncher.DesktopMode.code(folder: URL)` from Task 1 (via the existing `launch(_: ClaudeLauncher.Target)` helper at line 116, which is unchanged).

- [x] **Step 1: Replace `launchClaudeMenu` with the split control**

Replace the entire `launchClaudeMenu` computed property (lines 60–105) with:

```swift
    /// Split control: the primary button launches the default target (a new
    /// Claude Code session in Claude Desktop); the chevron opens the full menu.
    private var launchClaudeMenu: some View {
        HStack(spacing: 0) {
            Button {
                launch(.claudeDesktop(.code(folder: scoutDirectory)))
            } label: {
                HStack(spacing: 5) {
                    Image(systemName: "sparkles")
                        .font(.system(size: 10))
                    Text("Launch Claude Code")
                        .font(DS.sans(11.5, weight: .medium))
                }
                .foregroundStyle(DS.Ink.p3)
                .padding(.leading, 10)
                .padding(.trailing, 4)
                .frame(height: 24)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plainHit)

            Menu {
                Button {
                    launch(.claudeDesktop(.code(folder: scoutDirectory)))
                } label: {
                    Label("Claude Desktop — new Claude Code session",
                          systemImage: "chevron.left.forwardslash.chevron.right")
                }
                Divider()
                Button {
                    let config = CLIConfig(
                        claudePathOverride: claudeCLIPath,
                        terminal: CLITerminal(rawValue: cliTerminal) ?? .auto,
                        customCommand: customLaunchCommand
                    )
                    launch(.cli(cwd: scoutDirectory, config: config))
                } label: {
                    Label(cliMenuLabel, systemImage: "terminal")
                }
                Divider()
                Button {
                    launch(.claudeDesktop(.chat))
                } label: {
                    Label("Claude Desktop — new Chat", systemImage: "bubble.left.and.bubble.right")
                }
                Button {
                    launch(.claudeDesktop(.cowork))
                } label: {
                    Label("Claude Desktop — new Cowork task", systemImage: "person.2")
                }
            } label: {
                Image(systemName: "chevron.down")
                    .font(.system(size: 8))
                    .foregroundStyle(DS.Ink.p4)
                    .padding(.horizontal, 6)
                    .frame(height: 24)
                    .contentShape(Rectangle())
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
        }
        .onHover { hovering in
            if hovering { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
    }
```

Notes:
- `cliMenuLabel` (lines 107–114) and `launch(_:)` (lines 116–123) are unchanged.
- The hover cursor moves to the enclosing `HStack` so both segments share it.

- [x] **Step 2: Build**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild build -scheme Scout -destination 'platform=macOS' 2>&1 | grep -iE "error:|BUILD (SUCCEEDED|FAILED)"`
Expected: BUILD SUCCEEDED.

- [x] **Step 3: Run the launcher test suites**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ClaudeDesktopURLTests -only-testing:ScoutTests/CLILauncherTests -only-testing:ScoutTests/ClaudeLauncherPromptTests 2>&1 | grep -iE "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: PASS.

- [x] **Step 4: Commit**

```bash
git add Scout/ActionItems/Views/TaskActionsView.swift
git commit -m "feat(ui): make Claude Desktop Code session the default Launch Claude action

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Full-target regression run

**Files:** none (verification only)

- [x] **Step 1: Run the whole ScoutTests target**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests 2>&1 | grep -iE "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: TEST SUCCEEDED (no regressions).

- [x] **Step 2: Manual smoke check (needs Jordan or a GUI session)**

With Claude Desktop installed: open Scout → Action Items → any task →
click **Launch Claude Code**. Expect Claude Desktop to front on the Code
tab's new-session composer with the task prompt prefilled and the vault
folder pending trust confirmation (first time only). Then open the chevron
menu and confirm all four options are present and the CLI option still works.
