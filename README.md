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

**Download Scout.app** — starting with Scout 0.16, that's the whole install.
Grab the latest `Scout-*.dmg` from
[Releases](https://github.com/Raven-Scout/Scout/releases), drag **Scout.app**
into Applications, and open it. Onboarding takes it from there: it checks for
Claude Code (offering to open Terminal with Anthropic's installer, and asking
you to sign in, if it's missing), installs `uv` and the Scout engine under
`~/.local/share/scout`, registers the engine with Claude Code, asks for your
name, email and connectors, creates your vault (default `~/Scout`), and
schedules the sessions. Updating the app updates the engine.

Already set up via `install.sh`, a Claude Code marketplace install, or a dev
checkout? The app adopts it as-is — it never modifies an install it didn't
create — and shows where it lives in Settings ▸ Engine.

Prefer the terminal, or don't want the app? `install.sh` is the no-app path —
it sets up the plugin and engine:

```bash
curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash
```

It installs the plugin and engine, then walks you through setup (your details, connectors, budget) and creates `~/Scout/`.

`install.sh`, `/scout-update` and `claude plugin install|update scout@scout-plugin`
all get the latest release's plugin, never unreleased work on `main`: the
marketplace pins the plugin to the `stable` branch, which moves only when a
release is published.

> **Installed from `Raven-Scout/scout-plugin`?** Nothing to do. This repo *is*
> scout-plugin, renamed to `Raven-Scout/Scout` when the macOS app moved in, and
> GitHub redirects the old name, so `/scout-update` keeps working. The app's
> pre-move repo, issues and releases are archived at
> [`Raven-Scout/scout-app-legacy`](https://github.com/Raven-Scout/scout-app-legacy).

## Releases

Since v0.16.0 the app and the plugin share one version and ship as one GitHub
release, tagged `vX.Y.Z` and cut with `scripts/release.sh`. Publishing a
release also fast-forwards the `stable` branch to the release commit, which is
what marketplace installs read. Release candidates (`vX.Y.Z-rc.N`) never move it.
The earlier `plugin/vX.Y.Z` and `app/vX.Y.Z` tags are history.

**Release gate: KB writing.** On the release branch, before merging its PR, the
maintainer runs `plugin/scripts/eval.sh kb-gate` on their own Claude login. It
starts one consolidation session (Sonnet; `--opus` for Opus) against a throwaway
copy of a synthetic vault whose project front page meets the KB page standard,
with the `kb lint` front-page lock on. It feeds the session a day of new signals
about that project, and fails when the run broke the page:
- `kb structure` no longer shows the folder ✓;
- the front page grew by more than 512 bytes (`--max-growth N`);
- the new facts didn't land in the project's `knowledge/` notes, cited to a URL
  or to an existing source or meeting note;
- the front page gained narration, a dated or "update" bullet, a new paragraph,
  or a bullet over the 300-character cap.

Exit 0 is a pass, 1 a fail, 2 a run that couldn't be scored. The JSON report it
prints (also saved in the result dir as `kb-gate-report.json`) names the commit
it ran on: paste it into the release PR, check that commit is the release
branch's head, and don't merge on a fail.
`plugin/scripts/eval.sh kb-score <result dir>/vault` re-scores a kept run
without starting a session. CI never runs the gate, because it spends model usage.

Before merging a release PR, the maintainer also runs the agent-behaviour eval
locally, on their own Claude login (Sonnet, no API key):
`plugin/scripts/eval.sh run <scenarios>`, or
`plugin/scripts/eval.sh compare <scenarios> --base <last tag>` when
`plugin/phases/` changed. Then they paste the summary it prints into the release PR.
`release.sh prepare` prints this step with the date of the newest local result.
It is a reminder, never a gate. CI runs no model-backed evals.

They also rehearse the headless update from the previous release to the new one, in a
throwaway Linux container: `scripts/e2e/self-update/run.sh --from <last tag> --to
release/vX.Y.Z`, or the manual **e2e-self-update** workflow. `release.sh prepare`
prints this step too, and the release PR has a checkbox for it. It is not a gate. See
[`scripts/e2e/self-update/README.md`](scripts/e2e/self-update/README.md).

## Legal

MIT — see [LICENSE](./LICENSE), [PRIVACY.md](./PRIVACY.md), [TERMS.md](./TERMS.md).
