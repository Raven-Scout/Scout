---
phase: core
name: git-trailers
slot: setup
mode: [briefing, consolidation, dreaming, research]
requires: null
---

## Commits That Change {{INSTANCE_NAME}} Itself

**A commit to `SKILL.md`, `DREAMING.md`, `RESEARCH.md` or `scripts/` is a change on the changes map**, tested for whether it helped. Commit each on its own, with trailers after a blank line: `Scout-Change-Why:` (`pattern-<N>` for a mistake-audit pattern, `owner-ask:<Slack ts>` for {{USER_NAME}}'s message, `wishlist:<slug>` or `research:<slug>`) and `Scout-Change-Summary:` (what changed, one plain sentence of at most 120 characters). Optional `Scout-Change-Targets:`: comma-separated `pattern-<N>`s it should reduce, or `engagement` (at most one such change per run, and only when `scripts/scoutctl changes due --json` shows `engagement_tests_running` is 0). A change to what {{INSTANCE_NAME}} may do on its own goes to `governance-pending/` instead (feedback processing, Step 1d). Write the message in plain double quotes, each trailer on its own line; inside the quotes, never write `"`, `$`, a backtick or a backslash:

```bash
scripts/git-safe-commit.sh "dreaming [HH:MM]: SKILL.md — re-query issue status before reporting it

Scout-Change-Why: pattern-<N>
Scout-Change-Summary: Briefings re-check an issue's status live before reporting it." SKILL.md
```
