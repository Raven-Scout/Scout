# Changelog fragments

A PR never edits `plugin/CHANGELOG.md` or `apps/macos/CHANGELOG.md`. It adds one file per section it
has entries in, here for the plugin and engine or in `apps/macos/changelog.d/` for the app:

```
plugin/changelog.d/<branch-slug>.<section>.md
```

- **`<branch-slug>`** is your branch name after its last `/`, in lowercase letters, digits and hyphens
  (`claude/fix-run-ledger-a1b2c3` → `fix-run-ledger-a1b2c3`), so a merge never makes another PR conflict.
  If that part is generic (`fix-docs`) or a branch name you used before, add a short random suffix
  (`fix-docs-7k2p`): `jordan/fix-docs` and `claude/fix-docs` would otherwise pick the same file.
- **`<section>`** picks the heading: `added`, `changed`, `fixed`, `removed`, `security`, `deprecated`,
  `performance`, `documentation` or `upgrade-notes`. Entries in two sections are two files
  (`fix-run-ledger-a1b2c3.fixed.md` and `fix-run-ledger-a1b2c3.changed.md`).
- **The content** is the entry exactly as it would read in the CHANGELOG: one or more `- ` bullets, with
  indented sub-bullets if needed. No headings. Paths in the plugin's entries are plugin-relative.

```markdown
- **The run ledger names the log of each run** (`engine/scout/run_ledger.py`). …
```

`scripts/release.sh prepare` writes every fragment into the new `## [X.Y.Z] - date` section, grouped by
heading, and deletes them in the release commit. Nothing else moves them. Within a heading, entries
still under `## [Unreleased]` come first, then the fragments **in file-name order, not merge order**.

CI's `contract` job runs `python -m scout.scripts.changelog check`. On every run, every fragment must
parse. On pull requests and in the merge queue (not on the push to main that follows, which a squash
merge would trip), it also compares with the base, and fails when:

- a PR changes a fragment main already has (a slug collision, or a reused branch name). Deleting one
  is fine: a revert does that, and so does the release PR;
- a PR changes or drops a `## [X.Y.Z]` section main already has. That is what a plain merge of main
  does when it files your `[Unreleased]` entry under a release just cut.

**Fixing a release in progress.** A PR into `release/vX.Y.Z` edits the `## [X.Y.Z]` section of the
CHANGELOG directly, and must not add a fragment: fragments there are never assembled into X.Y.Z, so the
fix would ship in X.Y.Z but be listed under the next release. `check` allows exactly that, for that
version only.

**Fixing an entry of a published release on purpose** (a typo, a wrong path): end one commit's message
with the trailer `Changelog-Edit-Released: X.Y.Z <why>`, naming the version. It allows edits to that
version's section only, and only a real trailer counts (git parses the closing trailer block). The
PR's own commits carry it; a squash merge drops it, which is why the push to main doesn't re-check.
