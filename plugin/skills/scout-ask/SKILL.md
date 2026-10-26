---
name: scout-ask
description: Use when the working directory is a Scout vault (it has `knowledge-base/` and `action-items/`) and the user asks about the state of a project, initiative, person, decision, meeting or open thread ("where are we on X", "what's going on with Y", "who is Z", "what's on my plate"), or what Scout knows about a topic. Do not use for code, PR, CI or branch questions in a code repository (including Scout's own source), when no vault is present, or when the user asks for a live Slack, Linear, GitHub or Gmail search.
---

# Asking Scout

Scout has already done the investigation. Every KB file is a synthesis that a prior session built from live connectors and stamped with the date it was verified. Answering is **retrieval against a canonical file**, not a fresh investigation, and the vault is laid out so one file holds the answer.

Work the three steps in order. Most questions are done after step 2. Every path below is relative to the vault root.

## 1. Resolve the entity to its canonical file

One cheap listing beats a vault-wide search. For a project or initiative:

```bash
ls knowledge-base/projects/          # match the user's words to a slug
```

The listing also shows `projects.md` (the registry) and `archived/`. Neither is a project; look inside `archived/` only when the user asks about a finished or shelved project.

Then read `knowledge-base/projects/<slug>/<slug>.md`. That single file is the canonical record: it carries the header status *and* every dated update in reverse-chronological order.

Use the map below for other entity types. When the entity resolves to a file, you are done searching: read it and go to step 2.

**When nothing matches**, search the entity files rather than the vault:

```bash
rg -l -i "<entity>" knowledge-base/projects knowledge-base/topics knowledge-base/sources knowledge-base/people knowledge-base/ontology/entities
```

Never read `knowledge-base/knowledge-base.md` whole: on older vaults it runs to hundreds of thousands of tokens. Reach it only through a scoped `rg` with line numbers, after the entity files come up empty.

## 2. Read the freshness gate, then answer

Two timestamps sit at the top of the file. The frontmatter's `last_updated:` is the last substantive change. The `**Last verified:** <timestamp> (<session type>)` line under the title, where present, is the last time a session checked the file against its sources even if nothing changed. Gate on the newer of the two:

- **Recently verified**: answer from the file and cite the timestamp. The recorded state *is* the current state; a live re-query returns the same facts and costs several turns.
- **Old enough that the answer could mislead, or neither timestamp present**: answer from the file, say how old it is, and offer to drill.

The file's own hedges are load-bearing and belong in your answer: `[unverified]`, `[single-source]`, "not yet decided - do not record a verdict". Carry them through rather than flattening them into fact.

**You are done when you can state:** the status and owner, what moved most recently, what is still open, and which of it is the user's. The canonical file's header plus its newest dated update almost always supplies all four. Reaching for action items, meeting notes, or a second project file after that is re-investigating what the file already told you.

## 3. Drill to live sources only when the gate opens

Drill when the gate is stale, when the user asks about something the file marks unresolved, or when they explicitly ask you to re-check.

When you do, **use the reference the KB already recorded**: every synthesis cites its source with the exact coordinates (Slack channel + thread ts, `gh pr view <n> --repo <org>/<repo>`, Linear issue IDs, Drive doc IDs). Go straight to that coordinate. Re-discovering it through a search or an issue-list query is the expensive path Scout ran so you wouldn't have to.

## Map

| Question | Canonical file |
|---|---|
| Project / initiative status | `knowledge-base/projects/<slug>/<slug>.md` |
| A durable topic that is not a project | `knowledge-base/topics/topics.md`, then the domain index `topics/<domain>/<domain>.md` and the topic note it links |
| A decision, or what was said in a specific meeting, doc or thread | `knowledge-base/sources/<YYYY-MM>/<YYYY-MM-DD>-<slug>.md` (one source note per event; `knowledge-base/sources/sources.md` indexes them) |
| A person | `knowledge-base/people/<name>.md`, then `knowledge-base/people.md`. Roster facts only; for current work, read the project files in its `works_on` |
| What's on my plate | `action-items/action-items-<YYYY-MM-DD>.md` (today's date) |
| A recurring meeting | `meetings/meetings.md` lists the tracked meetings; each has `meetings/<slug>/<slug>.md` (purpose, attendees, decisions) and dated `meetings/<slug>/<YYYY-MM-DD>.md` session files. Present only once meetings are being tracked; if `meetings/meetings.md` has no rows, there are none |
| What Scout did recently | `knowledge-base/session-log/<YYYY-MM>*.md`: this month's shard, which splits into `<YYYY-MM>-pNN.md` parts as it grows; read the newest part |
| Typed / graph query | `python3 knowledge-base/ontology/parser.py query --type task --status open` (`--help` for traverse, related, path) |
| A Slack channel | `knowledge-base/channels.md` |
| What Scout is unsure about | `knowledge-base/review-queue.md`, `knowledge-base/research-queue/` |

## Common mistakes

| Mistake | Instead |
|---|---|
| `ls` the vault root to orient | Go straight to the map; the layout is fixed |
| Vault-wide `grep` for the entity | `ls knowledge-base/projects/` and match the slug |
| Re-querying Linear/GitHub to confirm what the file states | Check the freshness gate; a recently verified timestamp is the confirmation |
| Loading MCP connector schemas before reading the KB | Read the file first; most questions never need a connector |
| Reading `knowledge-base/knowledge-base.md` to find where something lives | Entity files, then a scoped `rg` |
