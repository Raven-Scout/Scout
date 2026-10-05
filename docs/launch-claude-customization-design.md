# Launch Claude — configurable path & terminal target (#12)

Design for GitHub issue #12: make "Open in Claude Code" customizable —
user-defined `claude` binary path and terminal target.

Status: design approved 2026-06-14, pending spec review.

---

## Motivation

Today the action-item "Launch Claude" CLI path assumes one environment:
Ghostty.app + a running tmux session + a `claude` binary discoverable from
a small probe list (`ClaudeLauncher.resolveClaudePath()`). Users who don't
use Ghostty/tmux, or whose `claude` lives somewhere unprobed, get a window
that flashes open and closes, or nothing.

### Acceptance criteria (from #12)
1. A user with `claude` outside the probed locations can point Scout at it
   via Settings and launch successfully.
2. A user without Ghostty/tmux can pick Terminal.app (or another supported
   target) and get a working Claude Code session.

---

## Scope

**In scope**
- A `claude` binary path override in Settings.
- A terminal-target picker: **Auto**, **Terminal.app**, **iTerm2**,
  **Custom command**.
- A custom-command template with `{cwd}` / `{claude}` placeholders.

**Out of scope** (captured for later, not built here)
- kitty / WezTerm / Alacritty named targets — the custom-command template
  covers them.
- A `{prompt}` placeholder that injects the prompt into the launch command
  (the clipboard-paste model stays the universal mechanism).
- Task-relevant `cwd` resolution (BACKLOG item) — still always `~/Scout`.
- **Cross-platform (Linux/Windows) support** — see "Cross-platform stance".

---

## Cross-platform stance

The owner wants Scout to eventually run on Linux/Windows. That is a
UI-layer rewrite (SwiftUI + AppKit + launchd + `SMAppService` are all
macOS-only) and a separate strategic project — not part of #12.

What #12 does for that future: the **custom-command template is the one
portable primitive**. `{cwd}`/`{claude}` substitution through a shell maps
onto any OS (`gnome-terminal`/`kitty` on Linux, `wt.exe` on Windows). The
*named* targets (Terminal.app, iTerm2, Ghostty-via-`NSWorkspace`) are
inherently macOS-only.

Design consequence: `CLITerminal`'s named cases are macOS conveniences;
the `.custom` case is the platform-agnostic path. The enum is the seam a
future port extends.

---

## Settings model

New "Claude Code" card in `SettingsView`, three `@AppStorage` keys (all
backward-compatible — existing users see identical behavior):

| Key | Type | Default | UI |
|-----|------|---------|----|
| `claudeCLIPath` | String | `""` | Text field. Placeholder shows the auto-resolved path (or "Auto-detect" if none found) so the user sees what they're overriding. |
| `cliTerminal` | String (rawValue of `CLITerminal`) | `"auto"` | Picker: Auto / Terminal.app / iTerm2 / Custom command. |
| `customLaunchCommand` | String | `""` | Text field, shown only when `cliTerminal == custom`. Help text documents `{cwd}` and `{claude}`. |

The card lives between "General" and "Linear" (it's about how the app
launches external tools, closest in spirit to General).

---

## Launcher architecture

`ClaudeLauncher` stays a stateless enum of static methods (it is already
unit-tested via `ClaudeLauncherPromptTests`). Configuration is **injected**
from the call site, not read from `UserDefaults` inside the launcher —
this keeps the terminal/command logic pure and testable.

### New types
```swift
enum CLITerminal: String, CaseIterable, Identifiable {
    case auto, terminalApp, iterm2, custom
    var id: String { rawValue }
    var displayName: String { … }   // "Auto", "Terminal.app", "iTerm2", "Custom command"
}

struct CLIConfig {
    var claudePathOverride: String   // "" = none
    var terminal: CLITerminal
    var customCommand: String        // "" = none; only used when terminal == .custom
}
```

### Target change
`Target.ghostty(cwd:)` → `Target.cli(cwd: URL, config: CLIConfig)`.
The `claudeDesktop(DesktopMode)` cases are unchanged.

### claude path resolution
`resolveClaudePath(override:)`:
- If `override` is non-empty **and** `isExecutableFile(atPath:)`, use it.
- Otherwise the current probe: `~/.local/bin/claude`, `/opt/homebrew/bin`,
  `/usr/local/bin`, then login-shell `command -v claude`.
- A non-empty-but-non-executable override does **not** silently fall back;
  it surfaces `claudeCLINotFound` so a typo'd path is visible, not masked.

### Dispatch per terminal
- **`.auto`** — today's behavior, plus a fallback chain so Auto works for
  everyone: **Ghostty+tmux → fresh Ghostty window → Terminal.app**. (Was:
  error if Ghostty absent. Approved behavior change — satisfies acceptance
  criterion (2) even without touching settings.)
- **`.terminalApp`** — AppleScript `tell app "Terminal" to do script "…"`
  that `cd`s to `cwd`, prints the clipboard-paste hint, and `exec`s the
  resolved claude. Opens a new Terminal window/tab.
- **`.iterm2`** — AppleScript against iTerm2's dictionary: create a window
  with the default profile and `write text` the same `cd … && exec claude`.
- **`.custom`** — expand `{cwd}`/`{claude}` in the template, run via login
  shell (`zsh -lc "<expanded>"`) so PATH-dependent commands resolve.

The clipboard-paste model (context copied to pasteboard, user pastes ⌘V
into the claude TUI) is preserved uniformly across all targets — it is
already the documented universal fallback.

### Launch mechanics & escaping (the risk area)
Each named target builds a command string that embeds `cwd` and the claude
path. Two escaping contexts:
- **Shell** (Terminal.app body, iTerm2 body, custom command): double-quote
  with `\` and `"` escaped, matching the existing `makeGhosttyScript`.
- **AppleScript** (the `do script` / `write text` string literal): AppleScript
  string escaping (`\` and `"`), applied to the already-shell-escaped body.

These builders are pure functions and are where the unit tests focus.

---

## Menu changes (`TaskActionsView`)

The CLI menu item's label reflects the configured terminal:
- Auto → "Open in Ghostty → Claude Code"
- Terminal.app → "Open in Terminal.app → Claude Code"
- iTerm2 → "Open in iTerm2 → Claude Code"
- Custom → "Open in custom terminal → Claude Code"

The handler reads the three `@AppStorage` keys, builds a `CLIConfig`, and
calls `.cli(cwd: scoutDirectory, config:)`. Claude Desktop options unchanged.

---

## Error handling

New `LaunchError` cases, surfaced through the existing `launchError` banner
in `TaskActionsView`:
- `iterm2NotInstalled` — iTerm2 selected but `com.googlecode.iterm2` absent.
- `terminalLaunchFailed(String)` — AppleScript execution returned an error
  (covers Automation-permission denial; message points the user at
  System Settings → Privacy & Security → Automation).
- `customCommandEmpty` — Custom selected but `customLaunchCommand` is blank.

Existing cases (`claudeCLINotFound`, `ghosttyNotInstalled`, etc.) are kept.
`ghosttyNotInstalled` is no longer thrown by `.auto` (it falls through to
Terminal.app); it remains for any explicit Ghostty path if added later.

---

## Testing plan

Pure builders are extracted and unit-tested (all CI-safe — no real app
launches):
- `resolveClaudePath(override:)` — override precedence: executable override
  wins; blank override falls back to probe; non-executable override returns
  nil (→ `claudeCLINotFound`). Tested with a temp executable file.
- `expandCustomCommand(template:claude:cwd:)` — `{cwd}`/`{claude}`
  substitution; both placeholders; repeated placeholders; paths with spaces
  and quotes escaped correctly; empty template handling.
- `makeTerminalAppScript(claude:cwd:)` / `makeITermScript(claude:cwd:)` —
  produce the expected `cd … exec …` body with shell + AppleScript escaping
  for paths containing spaces, quotes, and backslashes.

The thin `NSWorkspace` / `NSAppleScript` invocation layer stays untested
(can't run headless); it is kept as small as possible so the tested pure
builders carry the correctness.

---

## Migration / backward compatibility

All three keys default to `auto` / empty string. An existing user who never
opens Settings gets exactly today's behavior, except Auto now additionally
falls back to Terminal.app if Ghostty is missing (strict improvement — the
old path errored). No data migration.
