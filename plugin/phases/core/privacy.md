---
phase: core
name: privacy
slot: privacy
requires: null
---

## Privacy: owner-only and shared surfaces

Some notes are **private** (`visibility: private`, or a private path default such as `knowledge-base/personal/`). Every `scoutctl kb` result labels them `[private]` (`"visibility": "private"` in `--json`).

**Read the KB through `scoutctl kb`:** `files`, `search`, `read`, `resolve`, `query`, `related`, `entity`, `name-lookup`, `delta` (the vault's history, in place of git's log and diff), `validate`, `stats`. Never shell `cat`, `rg`, `grep`, `find` or `ls` into `knowledge-base/` or `action-items/context/`; Read/Edit on a file you are changing is fine. If a command answers `denied`, drop that line of work. Don't `ls`, `find`, `cat` or Glob to orient: `.scout-cache/session-context.json` lists personal tasks and KB file dates, labelled. Read a note you aren't editing with `scripts/scoutctl kb read <path>`.

**Private content** (a private note's text, title, slug, `name:`, aliases, or a link to it) may go to **owner-only** surfaces: vault files (KB notes, the daily file, context notes, review queue, commit messages) and the owner's private Telegram chat. It never goes to a **shared** one: any Slack message (a DM to {{USER_NAME}} included), any connector write (mail, calendar, Linear, GitHub, Drive …), `drafts/`, engine back-ports and PRs. On a shared surface, don't say a private note exists and don't count private items. When a private item goes to an owner-only surface (the daily file, context notes, Telegram), copy its title exactly as written: don't paraphrase it, scrub it or tag it "(private)". The files keep its details verbatim too.

**Queries are shared.** Never put private content into connector or web search text. Check a query built from KB content first: `scripts/scoutctl kb check-query "<query>" --surface connector_query` (`web_query` for web search). On exit 1, drop it or rephrase it without the private terms; never send it, never stall.

**Slack: the outbound-draft step.** Write every Slack message to `.scout-cache/outbound-draft.md`, run `scripts/scoutctl kb check-outbound .scout-cache/outbound-draft.md --surface slack`, and send only on exit 0. On exit 1 (it prints line numbers only), move the flagged lines to the Telegram message or the daily file, or rephrase them without private names, titles or links, and re-check. Still failing after one rephrase: send only the lines that passed, plus "Some items are in your daily file." (checked too). If that fails too, or the check exits 2, or `scoutctl` can't run, send nothing to Slack: put the wrap in the daily file or Telegram and say so in the run notes. A false-positive block never drops the wrap: never skip it, never resend the same text.

**Never widen.** Only {{USER_NAME}} makes a private note normal, removes a `visibility:` line, adds a `%%privacy:allow%%` marker, sets `federation_scope` or edits the privacy policy. Never commit with `--no-verify`.

**Narrow when you write.** A new note built from private material gets `visibility: private`. Private material for an existing normal note goes into a private note linked from it, or a review-queue proposal to mark that note private. Never silently flip an existing note.
