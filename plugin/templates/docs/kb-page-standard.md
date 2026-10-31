# KB page standard

**Who this is for:** every session that writes a project page, a topic note, or a topic-domain index, or that files anything into a project folder.
**Why it exists:** a knowledge base written run by run drifts into long, stale pages full of run
narration that only an agent can read. The KB WRITE PROTOCOL in the brain files (`SKILL.md`,
`DREAMING.md`) says *where* a fact goes and how big a file may get. This says what a page has to
**look like** so a person can read it.

The test for every page: **could the user open it in front of a colleague without apologising?**
The colleague should get what it is, where it stands and what's next in under a minute, and be
able to click through to the detail.

This file is plugin-owned: upgrades refresh it. A vault may tailor it (its own examples, a stricter
budget); the upgrade keeps those edits and merges later plugin changes into them.

## Folder layout: a project folder holds everything about the project

Opening `projects/<name>/` should show the whole project, sorted by kind, with nothing loose except
the front page.

```
projects/<name>/
  <name>.md          the front page (this standard); its Go deeper section is the folder's map
  knowledge/         topic notes about this project: cited, rewritten in place, plus <name>-decisions.md
  documents/         other people's documents in condensed form (vision drafts, priorities docs, specs)
  research/          syntheses, investigations, research prompts, written by the user or the agent
  archive/           verbatim pre-restructure copies and retired material
```

- **Only the front page sits at the top of the folder.** Every other file goes in one of the four
  subfolders. Create a subfolder when its first file arrives.
- **Subfolders get no index file.** The front page's **Go deeper** section lists every file,
  grouped, one line each. Names like `knowledge/knowledge.md` would collide across projects.
- **Short filenames with no dates.** Write `widgets-vision-v3`, not
  `widgets-product-vision-v3-2026-09-29`. The date goes in frontmatter (`date:`). Filenames still
  have to be unique vault-wide, so prefix generic names with the project (`widgets-decisions`, not
  `decisions`). The one dated name is the archive copy a restructure makes (see the last section).
- **Material that isn't about the project leaves the folder.** A note about the agent's own
  mistakes, for example, belongs with the agent's own notes, not in the project it happened on.
- **`topics/<domain>/` is only for knowledge that spans projects or belongs to none**, e.g. a
  client relationship that several projects share, or an internal process no project owns.

## Rules for every page

1. **Write for a person who wasn't there.** On first use, spell out every acronym, internal tag
   (`#WIDGETS`), and issue or PR number, then link it. Write "the pricing API PR
   ([example-org/widgets#42](https://github.com/example-org/widgets/pull/42))", never a bare "#42".
2. **No run narration, anywhere in the body.** Leave out session names (`research`,
   `evening-consolidation`), slot numbers, masked times (`08:0x`), finding or section counters
   ("Finding 76", "§113"), "this run", status stamps like "RETIRES" or "RE-SURFACED", verification
   diaries, and the reasons a run picked a lane. The commit message and the session log hold all
   of that.
3. **Provenance goes in frontmatter and topic notes, not the page body.** A project page links to
   the topic note that holds the cited detail. When it was last checked and against what goes in
   `last_verified:` and `verified_against:`, never in a "Last verified" line or blockquote at the
   top.
4. **Plain sentences, short bullets.** Keep a bullet to two lines at most, with one bold phrase at
   most. No walls of bold. Use tables only for people, decisions and dates.
5. **Absolute dates.** Write "Oct 28", or "Oct 28, 2026" when the year matters. Never "yesterday" or
   "last run". Times only when they matter, labelled with their zone (`%Z`).
6. **Current state replaces old state.** When a fact changes, rewrite it where it sits. Old states
   live in git and in the topic notes' history sections, not as stacked dated paragraphs.
7. **Emoji only for status.** One status emoji in the header line (🟢 on track · 🟡 at risk ·
   🔴 blocked or urgent · ⚪ paused or done). No decorative emoji in headings.

## Project page — `projects/<name>/<name>.md`

**Target: under 8 KB. Hard budget: 15 KB.** About one screen of reading. Sections come in this
order, all required (write "None right now." rather than dropping a section).

```markdown
---
# ontology fields stay as they are: name, aliases, type: project, status, priority, relationships …
status: "Active"              # a short phrase, not a paragraph
summary: "One sentence: what this is."
as_of: 2026-10-31             # the date "Where it stands" was last made true
last_verified: 2026-10-31
verified_against: ["gh example-org/<repo>", "issue tracker project <name>", "[[sources/2026-10/…]]"]
---
# <Project name>

<2–3 sentences: what it is, who it's for, why it matters, and the user's role.>

**Status:** 🟢 <one phrase> · **Owner:** [[people/…]] · **User's role:** <one phrase> ·
**Links:** [repo](…) · [tracker](…) · [channel](…)

## Where it stands
_As of Oct 31, 2026._
- <up to 6 bullets of current state, the most important first. Each ends with a link to the
  topic note holding the detail.>

## What's next
| When | What | Who |
|---|---|---|
| <date> | <milestone or meeting> | [[people/…]] |

## Key decisions
| Date | Decision | Why |
|---|---|---|
| <date> | <the decision, one line> | <one line + link to topic note> |
<!-- up to 7, newest first. The full record is in knowledge/<name>-decisions.md -->

## Open questions
- **<question>**: who decides, and what's blocking. <link>
<!-- up to 5 -->

## People
| Person | Role on this project |
|---|---|

## Go deeper
- **<Area>**: [[<note>]], with one line on what you'll find there.
<!-- grouped by subject, then Documents and Research; every file in the folder appears here -->

## History
<one short paragraph on how the project got here. Then:> The full record is in
[[<name>-decisions]] and the pre-restructure page is
[[projects/<name>/archive/<name>-YYYY-MM-DD-pre-restructure]].
```

A large sub-area that is really its own page (a sub-project, a long reference table) becomes its
own note in `knowledge/`, linked from **Go deeper**.

## Topic note — `projects/<name>/knowledge/<topic>.md` (or `topics/<domain>/<topic>.md` when it spans projects)

**Budget: 10 KB.** It holds one subject: how something works, why something was decided, or what
something costs. Rewrite it in place as understanding improves.

```markdown
---
domain: <project or domain>
project: "[[projects/<name>/<name>]]"
as_of: 2026-10-31
---
# <Plain noun-phrase title: "How the widgets release pipeline works">

**In short:** <2–3 sentences a newcomer can stop after.>

## <Sections as the subject needs>
<Prose and tables. Every paragraph cites a source note or a permalink, e.g.
"([PR #42](…))" or "([[sources/2026-10/2026-10-08-widgets-sync]])".>

## History
<optional: dated bullets of how the understanding or decision changed, newest first>
```

The same no-narration rule applies here. "Verified 2026-10-25 by reading `code.py` at `main`" is
a citation, so keep it as a short trailing link. It is not a paragraph of method.

## Decisions note — `projects/<name>/knowledge/<name>-decisions.md`

This is the one place a dated log belongs. It's a single table, newest first:
`| Date | Decision | Who decided | Why | Source |`. The project page shows the top 7. Decisions that
stopped mattering stay here; they don't clutter the page.

## Domain index — `topics/<domain>/<domain>.md` (cross-project domains only)

A short intro line, then every topic note in the folder grouped under 3–6 plain headings, one line
each saying what the note answers. Link it from `[[topics]]`. A topic domain must not share a
basename with a project page (`topics/widgets/widgets.md` would collide with
`projects/widgets/widgets.md` and make bare `[[widgets]]` links ambiguous), so name it for the
subject, e.g. `widgets-platform`.

## Restructuring an existing over-long page

The dreaming run's nightly KB pass rebuilds one over-budget project page a night this way. Do the
same by hand for any page you restructure.

1. Copy the page verbatim to `projects/<name>/archive/<name>-YYYY-MM-DD-pre-restructure.md`.
2. Move the durable knowledge into topic notes in `projects/<name>/knowledge/` (cited, at most
   10 KB each) and the decisions into the decisions note. Sort every other loose file in the folder
   into `documents/`, `research/` or `archive/` (or out of the folder) with `git mv`, repointing
   every path-style link to it in the same commit. Don't drop facts. Superseded facts go into a
   topic's **History** section or stay only in the archive.
3. Check the current state against a live source before writing "Where it stands". The old page
   is a lead, not a fact: the existing KB is claims to verify, never facts to preserve.
4. Rewrite the page in place, at the same path, to this standard, so inbound links keep working.
   If other notes link to `[[<name>#<heading>]]`, keep that heading or repoint the links.
5. `scripts/scoutctl kb lint --report`, `scripts/scoutctl kb validate` and the vault's dangling
   wikilinks must not get worse. Count each before you start and again before you commit.
