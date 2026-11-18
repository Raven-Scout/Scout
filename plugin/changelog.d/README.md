# Changelog fragments

A PR never edits `plugin/CHANGELOG.md` or `apps/macos/CHANGELOG.md`. It adds one file per section it
has entries in, here for the plugin and engine or in `apps/macos/changelog.d/` for the app:

```
plugin/changelog.d/<branch-slug>.<section>.md
```

- **`<branch-slug>`** is your branch name after its last `/`, in lowercase letters, digits and hyphens
  (`claude/fix-run-ledger-a1b2c3` → `fix-run-ledger-a1b2c3`). No two open PRs share a branch, so no two
  share a file, and a merge never makes another PR conflict.
- **`<section>`** picks the heading: `added`, `changed`, `fixed`, `removed`, `security`, `deprecated`,
  `performance`, `documentation` or `upgrade-notes`. Entries in two sections are two files
  (`fix-run-ledger-a1b2c3.fixed.md` and `fix-run-ledger-a1b2c3.changed.md`).
- **The content** is the entry exactly as it would read in the CHANGELOG: one or more `- ` bullets, with
  indented sub-bullets if needed. No headings. Paths in the plugin's entries are plugin-relative.

```markdown
- **The run ledger names the log of each run** (`engine/scout/run_ledger.py`). …
```

`scripts/release.sh prepare` writes every fragment into the new `## [X.Y.Z] - date` section, grouped by
heading and ordered by file name, and deletes them in the release commit. Nothing else moves them.
CI's `contract` job runs `python -m scout.scripts.changelog check --base <base>`: every fragment must
parse, and no PR may change a `## [X.Y.Z]` section that main already has.
