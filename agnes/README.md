# Scout marketplace for Agnes

`agnes/.claude-plugin/marketplace.json` is a second marketplace manifest, only for Agnes.

The root `.claude-plugin/marketplace.json` pins Scout with a `git-subdir` source to the `stable` branch (#362).
Agnes serves no files for a `git-subdir` source; it lists the plugin in its catalog and stops there. This
manifest names the same plugin with a plain string source, `"./plugin"`, so Agnes serves the plugin's files.

## How Agnes must register it

- **Pin a release tag, `vX.Y.Z`. Never `main`, never a branch.** A string source serves whatever tree is checked
  out. On `main`, that would be unreleased code labelled with the last release's version. The tag is what makes
  this file as safe as the root one's `stable` pin.
- Read the manifest from `agnes/` (the marketplace's manifest directory), not from the repository root.
- Resolve `source` against the **repository root**, not against `agnes/`: `"./plugin"` is the repo's `plugin/`
  subtree. (Claude Code's own `claude plugin marketplace add` would resolve it against `agnes/` and find
  nothing, so don't point Claude Code at this directory; use the root marketplace there.)

## Keeping it in step

Its `version` is one of the release's version files. `versioning check` (CI: `plugin-lint` and `contract`) fails
when it drifts, and `scripts/release.sh prepare` bumps it with the others. Never edit the version by hand. CI
also checks that its source stays `"./plugin"`.
