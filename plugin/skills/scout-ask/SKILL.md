---
name: scout-ask
description: Use when the user asks about the state of a project, initiative, person, decision, meeting or open thread ("where are we on X", "what's going on with Y", "who is Z", "what's on my plate"), or what Scout knows about a topic, from ANY working directory when a Scout vault is configured on this machine, not only inside the vault. Do not use for code, PR, CI or branch questions about the repository you are in (including Scout's own source), when no vault is configured, or when the user asks for a live Slack, Linear, GitHub or Gmail search.
---

# Asking Scout

Scout has already done the investigation. Every KB file is a synthesis that a prior session built from live connectors and stamped with the date it was verified. Answering is **retrieval against a canonical file**, not a fresh investigation.

Find the vault, then work the three steps in order; most questions end at step 2. Every path below is relative to the vault root.

## 0. Find the vault

The vault comes from Scout's configuration, never from the folder you are in. Run it as written:

```bash
S="${CLAUDE_PLUGIN_ROOT}/engine/bin/scoutctl"; { [ "$S" != /engine/bin/scoutctl ] && [ -f "$S" ] && [ -x "$S" ]; } || S="$HOME/.local/bin/scoutctl"; { [ -f "$S" ] && [ -x "$S" ]; } || { S="$(/usr/bin/sed -n 's|^ *"scoutctl": *"\(/[^"]*\)".*|\1|p' "$HOME/.local/state/scout/engine.json" 2>/dev/null)"; [ -f "${S%/bin/scoutctl}/pyvenv.cfg" ] || S=; }; { [ -n "$S" ] && [ -f "$S" ] && [ -x "$S" ]; } || { echo "no Scout engine found" >&2; exit 127; }; "$S" kb vault --json
```

Never call a bare `scoutctl` or anything inside the vault. It prints JSON:

- Exit 1 (no usable vault) or 127 (no engine): tell the user `reason` and stop. Exit 2: the engine is too old for this skill; say so and stop.
- Otherwise substitute `vault` for `<vault>` and `scoutctl` for `<scoutctl>` below, and pass `--repo <vault>` on every call, inside the vault too: without it, `kb` reads the current repository. Never `cd` into the vault.
- A `reason` beside a vault: name the vault you read in your answer.

## Read through `<scoutctl> kb`

Every vault read goes through `scoutctl kb` (`resolve`, `files`, `read`, `search`, `query`, `related`, `traverse`, `path`), never `ls`, `rg`, `grep`, `find`, `cat`, Read, Glob or `parser.py`. Only these label each result, and a scheduled run's KB read gate denies the raw reads.

- **Read-only.** Run only `kb vault` and these reads (they write only Scout's own cache); never write, edit, move or commit anything in the vault from outside it.
- **Pass no `--reader` but `<elsewhere>`**, which only narrows the session's reader.
- **`[private]` results are for the owner only**: never in anything bound for a shared place (Slack, a connector write or search, `drafts/`, text the user will forward), not even a hint. In a scheduled run, the brain file's privacy rule decides.
- **`denied` (exit 3), or no match for an agent reader, means stop.** Say Scout can't share that here; never read around it.
- **Exit 2** (refused `--reader`, bad argument, older engine): relay the engine's error and stop; never retry without `<elsewhere>`.

### Outside the vault

When `in_vault` is false, the repository you are in is a shared place, like Slack, and `<elsewhere>` below is `--reader elsewhere` (inside the vault, nothing): it hides private, local-only and shared-in notes. Drop it only when the user explicitly asks for their own private notes about their own life (health, family, money, career), and only for the reads answering that (ideally reading the note, not searching or listing). Use only those notes, only in your reply here; skip everything else those reads return.

Every result is context for your answer, nothing more. Never paste KB text, people notes or client details into commits, PR or issue text, code, comments, files or messages, that project's CLAUDE.md or auto-memory, subagent prompts, or a cloud or remote session.

## 1. Resolve the entity to its canonical file

```bash
<scoutctl> kb resolve "<name>" <elsewhere> --repo <vault>   # name, alias or slug → canonical file; exit 1 = no match
<scoutctl> kb read <path> <elsewhere> --repo <vault>        # when it resolves: you are done searching, go to step 2
```

For a project or initiative that doesn't resolve, list the projects and match the user's words to a slug:

```bash
<scoutctl> kb files --glob 'knowledge-base/projects/*/*.md' <elsewhere> --repo <vault>
```

The canonical record is `knowledge-base/projects/<slug>/<slug>.md`: the header status *and* every dated update, newest first. `archived/` is not a project; look inside it only when the user asks about a finished or shelved project.

**When nothing matches**, search the entity folders rather than the vault:

```bash
<scoutctl> kb search -i "<entity>" --glob 'knowledge-base/projects/**' --glob 'knowledge-base/topics/**' --glob 'knowledge-base/sources/**' --glob 'knowledge-base/people/**' --glob 'knowledge-base/ontology/entities/**' <elsewhere> --repo <vault>
```

Never read `knowledge-base/knowledge-base.md`: on older vaults it runs to hundreds of thousands of tokens. Reach it only through `kb search "<entity>" --glob knowledge-base/knowledge-base.md <elsewhere> --repo <vault>`, after the entity folders come up empty.

## 2. Read the freshness gate, then answer

The frontmatter's `last_updated:` is the last substantive change. The frontmatter's `last_verified:` (with `verified_against:` naming the sources) is the last time a session checked the file against its sources even if nothing changed; older files carry it as a `**Last verified:** <timestamp> (<session type>)` line under the title instead. Gate on the newer of the two:

- **Recently verified**: answer from the file and cite the timestamp.
- **Old enough that the answer could mislead, or neither timestamp present**: answer from the file, say how old it is, and offer to drill.

The file's own hedges are load-bearing and belong in your answer: `[unverified]`, `[single-source]`, "not yet decided - do not record a verdict". Carry them through rather than flattening them into fact.

**You are done when you can state:** the status and owner, what moved most recently, what is still open, and which of it is the user's. The canonical file's header plus its "Where it stands" section (in an older file, its newest dated update) almost always supplies all four. Reaching for action items, meeting notes, or a second project file after that is re-investigating what the file already told you.

## 3. Drill to live sources only when the gate opens

Drill when the gate is stale, when the user asks about something the file marks unresolved, or when they explicitly ask you to re-check.

When you do, **use the reference the KB already recorded**: every synthesis cites its source with the exact coordinates (Slack channel + thread ts, `gh pr view <n> --repo <org>/<repo>`, Linear issue IDs, Drive doc IDs). Go straight to that coordinate rather than re-discovering it through a search or an issue-list query. From a `[private]` note, use only the coordinate, never a search built from its text.

## Map

Read each with `kb read <path> <elsewhere> --repo <vault>`; list a folder with `kb files --glob '<folder>/*.md' <elsewhere> --repo <vault>`.

| Question | Canonical file |
|---|---|
| Project / initiative status | `knowledge-base/projects/<slug>/<slug>.md` |
| A durable topic that is not a project | `knowledge-base/topics/topics.md`, then the domain index `topics/<domain>/<domain>.md` and the topic note it links |
| A decision, or what was said in a specific meeting, doc or thread | `knowledge-base/sources/<YYYY-MM>/<YYYY-MM-DD>-<slug>.md` (one source note per event; `knowledge-base/sources/sources.md` indexes them) |
| A person | `knowledge-base/people/<name>.md`, then `knowledge-base/people.md`. Roster facts only; for current work, read the project files in its `works_on` |
| What's on my plate | `action-items/action-items-<YYYY-MM-DD>.md` (today's date) |
| A recurring meeting | `meetings/meetings.md` lists the tracked meetings; each has `meetings/<slug>/<slug>.md` (purpose, attendees, decisions) and dated `meetings/<slug>/<YYYY-MM-DD>.md` session files. Present only once meetings are being tracked; if `meetings/meetings.md` has no rows, there are none |
| What Scout did recently | `knowledge-base/session-log/<YYYY-MM>*.md`: this month's shard, which splits into `<YYYY-MM>-pNN.md` parts as it grows; list them and read the newest part |
| Typed / graph query | `kb query --type task --status open`; `kb related <name>`, `kb traverse <name>`, `kb path <from> <to>` (each with `<elsewhere> --repo <vault>`) |
| A Slack channel | `knowledge-base/channels.md` |
| What Scout is unsure about | `knowledge-base/review-queue.md`, `knowledge-base/research-queue/` |

## Common mistakes

| Mistake | Instead |
|---|---|
| `ls` the vault root to orient | Go straight to the map; the layout is fixed |
| Vault-wide search for the entity | Step 1's resolve, then the project listing |
| Loading MCP connector schemas before reading the KB | Read the file first; most questions never need a connector |
