# Scout.app

A macOS companion app for the [Scout](https://github.com/Raven-Scout/scout-plugin) Claude Code plugin.

Scout is an autonomous knowledge-management and daily-briefing system that runs as scheduled Claude Code sessions. The plugin does the work; this app gives you a native interface on top of whatever Scout produces in `~/Scout/`:

- **Control Center** — sessions activity, upcoming schedule, recent runs with cost/status, usage heatmap, on-battery banner.
- **Action Items** — today's to-do list rendered from the daily markdown file, with inline comments and deep links to Linear / GitHub PRs / Slack threads.
- **Agent handoff** — copy one or several action items as full context, concise text, or a Markdown checklist, or launch directly in Claude Code or Claude Desktop.
- **Schedules** — full CRUD editor for `~/Scout/.scout-state/schedule.yaml`. Master/detail layout with Table + Cards view toggle, type-color palette (briefing/consolidation/dreaming/research/manual), filter chips, live header. Atomic saves via `scoutctl schedule validate --target` with mtime stale-check + header-comment preservation. Click a row to edit its time, weekdays, on-miss policy, cooldown, runner, etc.
- **Proposals / Wishlist / Research** — review Scout's self-improvement proposals, its feature wishlist, and its research queue, one card per item.
- **Knowledge Base** — browse the `~/Scout/knowledge-base` notes with backlinks and a graph view.
- **Menu-bar control** — see current status and upcoming runs, fire a run, or open the full Control Center from a compact panel.
- **Budget + notifications** — edit the daily budget and window from Settings, and get a macOS notification when a scheduled run fails or hits a rate limit.

## Install

Scout.app sits on top of the Scout engine, so set up the engine first. (Making the app install the engine for you is on the roadmap: [#115](https://github.com/Raven-Scout/Scout/issues/115).)

**You need:** a Mac on macOS 13+, a paid Claude plan (Max recommended), and [Claude Code](https://docs.claude.com/claude-code) installed and signed in (run `claude` once in Terminal). Turn on the tools you want Scout to read at [claude.ai/settings/connectors](https://claude.ai/settings/connectors) — Slack is strongly recommended, since Scout's daily summary arrives as a Slack DM. No Homebrew or Python needed.

1. **Install the engine.** In Terminal:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/Raven-Scout/scout-plugin/main/install.sh | bash
   ```
   It ends with `✅ Scout plugin + engine installed.` (On a brand-new Mac it first asks you to install Apple's Command Line Tools — accept, wait, re-run.)
2. **Create your vault.** Open Claude Code and run `/scout-setup`. It asks a few questions, detects your connected tools, creates `~/Scout/`, and installs the schedule.
3. **Install the app.** Download the latest `Scout-*.dmg` from [Releases](https://github.com/Raven-Scout/Scout/releases/latest), open it, and drag **Scout.app** into **Applications**. It's signed with a Developer ID and notarized by Apple, so it opens with a normal double-click.
4. **Configure.** Press ⌘, for Settings — see [First-run configuration](#first-run-configuration). Turning on **Launch Scout at login** is recommended.

The full install guide, troubleshooting and FAQ live in the [scout-plugin README](https://github.com/Raven-Scout/scout-plugin#install).

## Requirements (for building from source)

- macOS 13 (Ventura) or newer.
- Xcode 15 or newer (for build + codesign).
- An existing Scout instance at `~/Scout/`.

## Build & run

```bash
open Scout.xcodeproj
# In Xcode: Product → Run (⌘R)
```

Or from the command line:

```bash
xcodebuild -scheme Scout -destination 'platform=macOS' build
xcodebuild -scheme Scout -destination 'platform=macOS' test
```

### Dev build vs. release build (running both side by side)

Debug and Release builds use **different bundle identifiers** on purpose, so you can keep the stable app from the DMG installed in `/Applications` and simultaneously run a development copy out of Xcode without either one clobbering the other.

| Config | Bundle ID | Display name | Where it lives |
| --- | --- | --- | --- |
| Release (DMG install) | `com.scout.Scout` | Scout | `/Applications/Scout.app` |
| Debug (Xcode ⌘R) | `com.scout.Scout.dev` | Scout Dev | `~/Library/Developer/Xcode/DerivedData/.../Debug/Scout.app` |

Because the bundle IDs differ, they have **separate `UserDefaults`, separate menu-bar icons, and separate "Launch at login" registrations**. They still read/write the same `~/Scout/` directory — that's the intended shared state, since your dev build should see your real Scout data.

Typical workflow: keep "Scout" running from `/Applications` all day, and spin up "Scout Dev" from Xcode whenever you want to try a change. Quit the dev copy when you're done; the stable app keeps running unaffected.

## First-run configuration

Cmd+, opens Settings. A few fields are worth filling in:

- **Launch Scout at login** — start the app automatically so it's watching your Scout instance all day.
- **Start in menu bar** — launch with the full window hidden and use Scout from its compact panel until you need the Control Center.
- **Scout directory** — read-only display. The app assumes `~/Scout` (the scout-plugin default).
- **Claude CLI path** — leave blank to auto-detect (`~/.local/bin`, Homebrew, then your login shell). Used by **Launch Claude**; **Open Claude Code in** picks the terminal.
- **Budget** — the daily budget, rolling window, skip threshold and failure backoff the scheduled runs are gated by (written to `~/Scout/scout-config.yaml`, same as `scoutctl budget set`).
- **Linear workspace** — your Linear workspace slug (e.g. `acme-co`). Used to build Linear URLs when you click a `[[PROJ-123]]` wikilink or deep link in an action item. Leave blank to open `linear.app` without a workspace.
- **Your name** — shown next to comments you add to action items. Defaults to `user`.
- **Notify on failed runs / rate-limit** — macOS notifications when a scheduled run fails, times out, or is rate-limited.

## Repo layout

```
Scout/                 # main target source
  ActionItems/         # parser, writer, views for daily action-items markdown
  ControlCenter/       # sessions dashboard, upcoming-runs strip, on-battery banner
  KnowledgeBase/       # KB browser: note view, backlinks, graph
  Models/              # shared types (Run, Slot, UpcomingRun, Schedule, …)
  PerFileItems/        # Wishlist + Research tabs (one markdown file per item)
  Proposals/           # dreaming-proposals tab
  Schedules/           # master/detail editor for ~/Scout/.scout-state/schedule.yaml
  Services/            # file watcher, git, launchctl, plist I/O, ScheduleEditService
  Shell/               # AppState, sidebar, main window, settings, menu bar
  Utilities/           # DesignSystem (DS namespace) + helpers
ScoutTests/            # unit + integration tests (~900 @Test funcs / ~140 suites)
docs/                  # design specs + implementation plans
```

## Development

Tests run from the command line:

```bash
xcodebuild test -scheme Scout -destination 'platform=macOS'
```

Or a specific suite:

```bash
xcodebuild test -scheme Scout -destination 'platform=macOS' \
  -only-testing:ScoutTests/ScheduleEditServiceTests
```

The `ScoutTests/Fixtures/` directory holds synthetic plists, logs, and action-items files used by the suite. Nothing in them references a real person or incident.

## Cutting a release

Maintainers: `scripts/release.sh <version>` builds a universal (arm64+x86_64) DMG, signs the app with Developer ID + hardened runtime, notarizes and staples **both the app and the DMG** via Apple, tags `v<version>`, pushes the tag, and creates a GitHub Release with the DMG attached. Requires a `Developer ID Application` cert in the keychain and a `scout-notary` notarytool credential profile (see the header of `scripts/release.sh`). Example:

```bash
scripts/release.sh 0.2.0
```

Set `SKIP_RELEASE=1` to build the DMG locally without tagging or uploading.

## Relationship to the plugin

The plugin writes; the app reads (and occasionally writes back via `scoutctl` for action-item comments and schedule edits). The plugin owns:

- Schedule definition at `~/Scout/.scout-state/schedule.yaml` (10 default slots — briefings, consolidations, dreaming, research). The dispatcher fires every 5 minutes via the single `com.scout.schedule-tick.plist` launchd agent.
- Heartbeat agent `com.scout.heartbeat.plist` for opportunistic catch-up runs.
- Daily action-items markdown at `~/Scout/action-items/action-items-YYYY-MM-DD.md`.
- Session logs at `~/Scout/.scout-logs/*.jsonl` (connector calls, schedule events, session tokens).
- Usage tracking at `~/Scout/.scout-logs/usage-tracker.jsonl`.
- Commit history in `~/Scout/.git`.

The app is a pure consumer of all of the above, plus:

- Reads upcoming slots via `scoutctl schedule list-upcoming --json` (every 60s) for the Control Center strip.
- Reads + writes `~/Scout/.scout-state/schedule.yaml` via `scoutctl schedule list --json` and atomic-rename writes validated by `scoutctl schedule validate --target` from the Schedules tab editor.
- Saves comment edits via the plugin's action-items writer.
- Triggers a slot manually via `scoutctl schedule fire-now <slot-key>` (the Fire-now button in the Schedules detail pane and the Run-now button in the Upcoming strip).

If the plugin isn't installed, the app still builds and runs; it just shows empty views.

## License & legal

This app is open-source under the [MIT License](LICENSE).

Scout is local-first and collects no data of its own — the macOS app only reads and writes files in your local `~/Scout/` folder. See the project's shared legal documents:

- **Privacy Policy** — https://raven-scout.github.io/scout-plugin/privacy.html
- **Terms of Use** — https://raven-scout.github.io/scout-plugin/terms.html
- **[Security Policy](https://github.com/Raven-Scout/.github/blob/main/SECURITY.md)** · **[Code of Conduct](https://github.com/Raven-Scout/.github/blob/main/CODE_OF_CONDUCT.md)**

Scout is an independent project, not affiliated with Anthropic, Microsoft, or any other company.
