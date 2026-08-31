# Claude Desktop → Claude Code Launch Option (Default) — Design

**Date:** 2026-08-05
**Status:** Proposed (for review)
**Surface:** Action Items → task card → "Launch Claude" control

## Context

Each action item has a "Launch Claude" menu (`TaskActionsView.launchClaudeMenu`)
with three ways to open an interactive Claude session seeded with the task's
context:

1. **Claude Code CLI** — opens a terminal (Auto/Ghostty/Terminal.app/iTerm2/custom,
   configured in Settings) running `claude` in the Scout vault directory.
2. **Claude Desktop — new Chat** — `claude://claude.ai/new?q=<prompt>`.
3. **Claude Desktop — new Cowork task** — `claude://cowork/new?q=<prompt>`.

Today the control is a plain menu: clicking it always opens the dropdown, and
there is no default action. There is no way to launch the task into **Claude
Code inside the Claude Desktop app** (the desktop app's Code tab).

## Goal

Add a fourth launch target — a new Claude Code session in Claude Desktop's
Code tab, prefilled with the task prompt and pointed at the Scout vault
directory — and make it the **default**: a single click on the launch control
fires it, while the dropdown still offers all four options.

## Deep link (verified)

Claude Desktop documents a deep link for its Code tab
([support.claude.com article 14729294 — "Open Claude Desktop with a link"](https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link)):

```
claude://code/new?q=<url-encoded prompt>&folder=<absolute path>
```

- `q` — prefills the composer (~14,000 char limit). Never auto-sends; the user
  presses Enter.
- `folder` — absolute path adopted as the session's working directory. Claude
  Desktop treats it as untrusted and shows a one-time confirmation dialog
  (corroborated by the `ccd_deeplink_trusted_folder_skip_main` handling found
  in the installed Claude.app bundle).

This matches the two `claude://` prefill routes Scout already uses, so it
slots into the existing `ClaudeLauncher.Target.claudeDesktop` path — including
the copy-prompt-to-clipboard fallback that guards against flaky prefill.

## Design

### 1. `ClaudeLauncher` (Scout/Utilities/ClaudeLauncher.swift)

- `DesktopMode` gains a case with the working directory attached:

  ```swift
  enum DesktopMode {
      case chat
      case cowork
      /// `claude://code/new` — new Claude Code session in the Code tab.
      /// `folder` becomes the session working directory (Claude Desktop
      /// shows a one-time trust confirmation for it).
      case code(folder: URL)
  }
  ```

- URL construction moves out of the private `launchClaudeDesktop` into a pure,
  unit-testable builder (same pattern as `makeTerminalShellCommand` etc.):

  ```swift
  static func makeDesktopURL(prompt: String, mode: DesktopMode) -> URL?
  ```

  - `.chat`   → host `claude.ai`, path `/new`, query `q=<prompt>`
  - `.cowork` → host `cowork`,    path `/new`, query `q=<prompt>`
  - `.code`   → host `code`,      path `/new`, query `q=<prompt>&folder=<path>`

  Percent-encoding is handled by `URLComponents`/`URLQueryItem`, as today.
  `launchClaudeDesktop` keeps its existing behavior (bundle-ID guard →
  `.claudeDesktopNotInstalled`; clipboard fallback set by `launch`).

### 2. UI (Scout/ActionItems/Views/TaskActionsView.swift)

The single `Menu` becomes a split control — two adjacent borderless controls
styled as one unit (deterministic on macOS; avoids relying on
`Menu(primaryAction:)` click-vs-long-press quirks with `.borderlessButton`):

- **Primary button** — sparkles icon + "Launch Claude Code" text. Click fires
  `launch(.claudeDesktop(.code(folder: scoutDirectory)))` directly.
- **Chevron menu** — the existing dropdown, reordered so the default is first:
  1. Claude Desktop — new Claude Code session *(the default)*
  2. — divider —
  3. the CLI option (label unchanged, still driven by the Settings terminal picker)
  4. — divider —
  5. Claude Desktop — new Chat
  6. Claude Desktop — new Cowork task

Both segments keep the current 24pt height, hover cursor, and `DS` typography.

### 3. Settings — unchanged

The default is fixed, not a preference (YAGNI). The Settings "Open Claude Code
in" picker keeps governing only the CLI menu item; its help copy stays accurate.

## Error handling

- Claude Desktop not installed → existing `.claudeDesktopNotInstalled` message
  (download link) shown in the existing inline `launchError` text.
- The prompt is always copied to the clipboard first (existing behavior), so a
  dropped `q` param is recoverable with ⌘V.

## Testing

- New suite `ScoutTests/ActionItems/ClaudeDesktopURLTests.swift` covering
  `makeDesktopURL`: scheme/host/path per mode, `q` present for all modes,
  `folder` present only for `.code`, and round-trip decoding of a prompt
  containing spaces, `&`, `#`, `?`, and newlines.
- Existing `CLILauncherTests` / `ClaudeLauncherPromptTests` unchanged and green.
- Manual (post-implementation): click launches Claude Desktop Code tab with
  prompt + folder; chevron menu still reaches all four options.

## Non-goals

- No new user preference for choosing the default target.
- No truncation handling for prompts beyond the ~14k `q` limit (clipboard
  fallback covers it; consistent with the existing chat/cowork modes).
- No attempt to reuse/resume existing Code sessions, pick branches, or bypass
  the folder trust dialog (not supported by the documented deep link).
- No changes to `ClaudeLauncher.prompt(for:)` or the CLI launch paths.

## Risks

- The `claude://code/new` route is documented but young; if a Claude Desktop
  update changes it, the failure mode is benign (Code tab opens without
  prefill; clipboard fallback applies).
- Users who habitually click "Launch Claude" expecting a dropdown will now
  trigger a launch. Mitigation: the launch is harmless (nothing auto-sends)
  and the chevron remains one click away.
