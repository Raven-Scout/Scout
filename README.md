# Scout

Autonomous knowledge management and daily briefing for Claude Code — a
scheduled engine that cross-checks your work tools and maintains a persistent,
Obsidian-compatible knowledge base, plus native apps to read and steer it.

## What's in here

| Path | What it is |
|---|---|
| [`plugin/`](./plugin) | The Claude Code plugin and its Python engine (`scoutctl`). This is the part that does the work. |
| [`apps/macos/`](./apps/macos) | The macOS menu-bar app — Control Center, Action Items, Knowledge Base, schedules. |
| [`docs/`](./docs) | Design specs, implementation plans and architecture notes for both — and the public website (`index.html`, `privacy.html`, `terms.html` and their assets). |

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

> **Installed from `Raven-Scout/scout-plugin`?** Nothing to do. This repo *is*
> scout-plugin, renamed to `Raven-Scout/Scout` when the macOS app moved in, and
> GitHub redirects the old name, so `/scout-update` keeps working. The app's
> pre-move repo, issues and releases are archived at
> [`Raven-Scout/scout-app-legacy`](https://github.com/Raven-Scout/scout-app-legacy).

## Releases

The two artifacts version independently, behind prefixed tags:
`plugin/vX.Y.Z` and `app/vX.Y.Z`. A release build of the app records the plugin
version it was built against as its minimum (`SCScoutPluginFloor`); the app
does not yet warn when your engine is older than that.

## Legal

MIT — see [LICENSE](./LICENSE), [PRIVACY.md](./PRIVACY.md), [TERMS.md](./TERMS.md).
