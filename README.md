# Scout

Autonomous knowledge management and daily briefing for Claude Code — a
scheduled engine that cross-checks your work tools and maintains a persistent,
Obsidian-compatible knowledge base, plus native apps to read and steer it.

## What's in here

| Path | What it is |
|---|---|
| [`plugin/`](./plugin) | The Claude Code plugin and its Python engine (`scoutctl`). This is the part that does the work. |
| [`apps/macos/`](./apps/macos) | The macOS menu-bar app — Control Center, Action Items, Knowledge Base, schedules. |
| [`docs/`](./docs) | Design specs and implementation plans for both. |

## Install

One command sets up the plugin and engine:

```bash
curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash
```

Then open Claude Code and run `/scout-setup` to create your vault.

The macOS app is a separate download — grab the latest `.dmg` from
[Releases](https://github.com/Raven-Scout/Scout/releases) (look for an
`app/v*` tag). It is optional: the engine is fully usable from Claude Code and
the CLI without it.

> **Moved from `Raven-Scout/scout-plugin`?** Re-point your marketplace:
> ```
> claude plugin marketplace remove scout-plugin
> claude plugin marketplace add Raven-Scout/Scout
> claude plugin install scout@scout-plugin
> ```

## Releases

The two artifacts version independently, behind prefixed tags:
`plugin/vX.Y.Z` and `app/vX.Y.Z`. The app declares a minimum plugin version and
tells you when your engine is behind.

## Legal

MIT — see [LICENSE](./LICENSE), [PRIVACY.md](./PRIVACY.md), [TERMS.md](./TERMS.md).
