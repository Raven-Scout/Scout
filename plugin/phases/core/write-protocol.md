---
phase: core
name: write-protocol
slot: kb-write-protocol
requires: null
---

## KB WRITE PROTOCOL — where every fact goes (checked by the pre-commit hook)

The vault is a folder tree of **small, linked documents**, not a journal. A git pre-commit hook (`scoutctl kb lint --staged`) flags a file over its budget, a run-diary heading or a line over the limit. By default (`kb_lint.mode: report`) it only logs them; in `block` mode it refuses the commit. Privacy findings always block. Follow the rules either way. Read this before writing anything.

### Every fact has one home

| What you have | Where it goes | Budget |
|---|---|---|
| A meeting, doc, PR or decision thread worth keeping | **Source note** `knowledge-base/sources/YYYY-MM/<YYYY-MM-DD>-<slug>.md`. Frontmatter: `kind` (meeting\|doc\|thread\|pr\|email\|other), `date`, `url` or connector id, `participants` (wikilinks). Body: what was said, key quotes, decisions. Write once; later edits are corrections only. | 8 KB |
| Durable knowledge: how a system works, an org or commercial fact, a domain concept | **Topic note**: about one project → `knowledge-base/projects/<name>/knowledge/<topic>.md`; spanning projects or belonging to none → `knowledge-base/topics/<domain>/<topic>.md`. Rewritten in place as understanding improves. **Every paragraph cites** a source note or a permalink. A cross-project domain folder has `<domain>/<domain>.md` as its index (never `index.md`); a project's `knowledge/` folder has none. Find the existing note first (`scripts/scoutctl kb search <term> --glob 'knowledge-base/projects/<name>/**'`, or `--glob 'knowledge-base/topics/**'`), and create a new one only when none fits. | 10 KB |
| A dated decision about one project | **One row** in the project's decisions note `knowledge-base/projects/<name>/knowledge/<name>-decisions.md`: `\| Date \| Decision \| Who decided \| Why \| Source \|`, newest first. The project page's Key decisions table shows the top 7. | 10 KB |
| Current status, next dates, key decisions, open questions, people | **Project page** `knowledge-base/projects/<name>/<name>.md`, written for a person to [[docs/kb-page-standard]]: fixed section order, current state only, linking to topic and source notes. Verification goes in the frontmatter (`last_verified:`, `verified_against:`), never in the body. A large sub-area becomes a topic note in the project's `knowledge/` folder. | target 8 KB, hard 15 KB |
| Something {{USER_NAME}} must do | **One action-item line** in today's daily file (see the action-items phase) **plus its context note** `action-items/context/<TAG>.md`: what and why, status, blocker, evidence, links to project/topic/source notes. Edit the note in place; list notes with `scripts/scoutctl kb files --glob 'action-items/context/*.md'`, read one with `scripts/scoutctl kb read <path>`. | line ≤ 500 chars + one sub-bullet; note ≤ 15 KB |
| What this run did | **The commit message**, plus **one row** in `knowledge-base/session-log/YYYY-MM.md` | row ≤ 500 chars |
| A person, org or technology record | Entity file under `people/`, `ontology/entities/`, `personal/`. Topic notes link to entities and never duplicate them. | 10 KB |

**Source-worthiness:** a meeting {{USER_NAME}} attended with decisions or new facts, a doc or PR that changes understanding, or a thread where a decision was made. Routine messages get no source note; a topic note may cite their permalink directly.

**Project folders hold everything about the project:** `<name>.md` (the front page) at the top, then `knowledge/` (topic notes and `<name>-decisions.md`), `documents/` (other people's documents, condensed), `research/` (syntheses, investigations) and `archive/` (pre-restructure copies, retired material). No other loose files, no index file in a subfolder, short filenames with no dates (the date goes in frontmatter). See [[docs/kb-page-standard]] §Folder layout.

**Cross-project domains:** `topics/<domain>/` is only for a subject that spans projects or belongs to none. Knowledge about one project goes in that project's `knowledge/` folder however many notes it has. A domain must not share a basename with a project page.

### Rules

1. **Edit, don't append.** A changed claim is rewritten where it sits. Never add a dated paragraph below the old one.
2. **No run narration in documents.** No session headings (`## §59 · 2026-09-07 (overnight-research …)`), no "this run found…", no per-run headline summaries, no "Demoted/Re-tiered by …" sections, no "Last verified …" blockquote or line at the top of a page (verification goes in `last_verified:` / `verified_against:` frontmatter). Git history and the commit message carry the narrative.
3. **Split before you exceed.** If an edit would push a file over its budget, first move content into the correct layer (a new topic, source or sibling file) and link it, then make your edit.
4. **Never rename or move existing files**, except to archive a project (kb-management), re-tag a context note (action-items), or sort a project's loose files into its subfolders while restructuring its page (kb-deep-work's nightly KB pass), each with `git mv` and every path-style link to the file repointed in the same commit. Splitting content *out* into linked notes is expected; the original path stays as the current-state page or index.

### When the hook blocks your commit

The hook output names the file, the check and the fix. Fix it and commit again. **Only after two failed fix attempts**, commit with `SCOUT_LINT_OVERRIDE="<why>" git commit …`. The override is logged to `.scout-logs/lint-overrides.log` and the next dreaming run must clean it up. It never lifts a privacy block: fix those. Never pass `--no-verify`.
