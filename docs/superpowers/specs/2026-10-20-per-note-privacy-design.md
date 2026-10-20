# Per-note privacy: `visibility`, `federation_scope` and one read gate — design

Status: proposed, awaiting Jordan's approval (2026-10-20). Spec only: nothing
here is built until Jordan approves it.
Issue: #453 (per-note private flag), extended for the Scout-in-Agnes work.
Owner of the decisions: Jordan (privacy semantics, folder lists, rollout).

## 1. Why

A Scout vault holds everything one person knows about their work and life in
one git repo: work projects next to family, health and money notes. Until now
the vault had one reader, its owner, so it never needed a privacy model.
Sensitivity is implied by folder, and a handful of notes carry an ad-hoc,
free-text `visibility:` line that nothing reads.

Two things change that:

1. **#453.** Scheduled runs surface notes over outbound channels (Slack DM,
   Telegram, the daily action-items file, digests). The owner needs a way to
   say "never surface this note on its own", and every autonomous path has to
   respect it. `surface_rule: do_not_surface` covers only `task` entities and
   only the daily file.
2. **Scout inside Agnes.** Scout becomes the engine and knowledge behind each
   person's personal "Chief of Staff" agent in Agnes (Jordan, 2026-10-20). The
   vault syncs to a hosted, owner-only remote, an Agnes indexer reads it, and
   later another person's agent may ask this person's agent questions. Jordan's
   ruling: **vault content is never disclosed to another person's agent until
   per-note privacy exists AND Agnes has a read-boundary design.** This spec is
   the first half and the Scout side of the second.

The #480 review of `scout-ask` found the deeper problem: skills read the vault
with raw `ls`, `rg`, file reads and `parser.py`. A prose rule in a skill can't
enforce privacy, because a scoped `rg` returns private notes anyway. So the
centre of this design is **one engine read command** that every reader goes
through, with the policy as data that the engine and the Agnes indexer both
load.

## 2. Decisions for Jordan

Each row has a recommendation; §3–§11 assume the recommendations. The open
ones are repeated at the end (§13) in priority order.

| # | Question | Recommendation |
|---|---|---|
| D1 | Field names and values | `visibility: normal \| private` and `federation_scope: none \| org \| [person:<email> \| group:<host group id>, …]` (§3). |
| D2 | Default for an unmarked note | `visibility: normal`; `federation_scope: none` (nobody). |
| D3 | "Never leaves the laptop": a third `visibility: local` value, or path-level? | **Path-level**, outside frontmatter, as Agnes proposes (§4.3). No `local` value. |
| D4 | Folder defaults: written into frontmatter, or loadable data? | **Data.** An engine-owned `privacy.yaml` (+ generated `privacy.snapshot.json`) that the engine and the indexer both load. Scout never rewrites frontmatter to apply a default (§5). |
| D5 | Can a scheduled run *read* a private note, or only never surface it? | **Can't read it.** The engine's read commands don't return private notes to an autonomous reader (§6.2). Stricter than #453's wording; matches the Agnes indexer rule. |
| D6 | Should `knowledge-base/personal/` default to private? | **No.** That would drop personal tasks from the owner's own briefing. `personal/` is local-only (never synced) and has a federation ceiling of `none`; owners mark single notes private. Consequence: a run in the cloud (Agnes) has no `personal/` and so no personal tasks; only laptop runs surface them. |
| D7 | Which folders may ever be shared with other agents? | A short opt-in list with ceiling `org` (§5.3); every other path has ceiling `none`. Jordan picks the list. |
| D8 | Who may widen a note's privacy? | Only the owner. A pre-commit check blocks a scheduled run's commit that widens `visibility` or `federation_scope` (§8.3). |
| D9 | Peer-agent reads before the read-boundary design exists | Hard off in the engine: `privacy.federation.enabled: false`, and reader `agent:*` gets nothing even for `federation_scope: org` (§7). |
| D10 | The 8 existing free-text `visibility:` lines | Read fail-closed now (any unknown value counts as `private`), and `/scout-update` offers to normalise them with the owner's OK (§10, phase P5). |
| D11 | Reader when `SCOUT_READER` is unset | `autonomous` if `SCOUT_MODE` is set, else `owner`, so the owner's interactive tools keep their private notes; every engine-launched run sets `SCOUT_READER` explicitly (§6.2). |

## 3. The fields

Both fields are optional YAML frontmatter keys on **any** markdown file in the
vault: an ontology entity, a project page, a topic, a source note, a context
note or a plain note. They are not entity-schema properties, so a note doesn't
need `name:`/`type:` to carry them.

### 3.1 `visibility`

| Value | Meaning |
|---|---|
| `normal` | The default. Any local reader may see it; scheduled runs may surface it. |
| `private` | Owner-only. The owner's interactive sessions read it normally. No autonomous reader (scheduled runs, an Agnes unattended run, memory capture) gets it, and no other person's agent ever does. It still syncs to the owner's hosted remote, labelled. |

Parsing rules (the same in the engine and the indexer):

- The value is a YAML string compared case-insensitively after trimming.
- **Absent** means "use the path default" (§5), which in v1 is `normal`
  everywhere.
- **Any other value** (`PRIVATE while drafting`, `secret`, a typo) counts as
  `private` and draws a kb-lint warning. Fail-closed: a misspelling hides a
  note, it never exposes one.
- A file whose frontmatter opens with `---` but doesn't parse as YAML counts as
  `private` (and `federation_scope: none`) and draws a kb-lint error.
- A file with no frontmatter at all uses the path defaults.

### 3.2 `federation_scope`

What another person's agent may learn from the note. **Inert until §7's gate
opens**; until then the engine and the indexer only parse, validate and
report it.

| Value | Meaning |
|---|---|
| `none` | The default. No other person's agent learns anything from it. |
| `org` | Any member of the owner's organisation on the host (in Agnes: the instance), through their own agent. |
| a list of principals | Only those. A principal is `person:<email>` or `group:<host group id>`, resolved by the host (§3.3). |

Rules:

- Order for "narrower wins": `none` < a principal list < `org`. A principal
  list intersected with `org` is the list; anything intersected with `none` is
  `none`.
- Any unparseable value counts as `none` and draws a lint warning.
- `visibility: private` forces `none`, whatever `federation_scope` says. Lint
  warns on the contradiction.
- A local-only path (§4.3) forces `none`.
- The effective scope is also capped by the folder ceiling (§5.3).

`public` (the open web) is deliberately not a value. Publishing a curated
slice outside the organisation is a separate, later design (§12).

### 3.3 Principals

Agreed with the Agnes spec (2026-10-20), pending Jordan's D1:

| Form | Syntax Scout checks | Agnes resolves it as |
|---|---|---|
| `person:<email>` | `^person:[^\s@,\[\]]+@[^\s@,\[\]]+$`, compared case-insensitively | the Agnes user with that email |
| `group:<host group id>` | `^group:[A-Za-z0-9_-]{1,64}$` (opaque to Scout) | the group with that id (Agnes: 32-hex) |
| `org` | the scalar value, not a list entry | the instance's Everyone group |

- Groups are named by the host's **stable id**, not a slug or display name:
  Agnes groups have no slug, and admins can rename display names. A rename
  must never widen or narrow what a note shares.
- Scout tooling that writes a group principal takes the id from the host.
  In Agnes that is the person's share-target lookup (`GET
  /api/sharing/groups`, also wrapped by the Agnes stdio MCP server), or the
  `agnes groups list --json` CLI verb once Agnes ships it. Lint and the UI
  show the group's current name next to the id when a lookup is available.
- Because that lookup returns only the person's own groups plus Everyone, an
  owner can only name groups they belong to (plus `org`), as in Agnes Library
  sharing (§7.2).
- **A principal the host can't resolve admits nobody.** That covers an
  unknown email, a deleted group, and the wrong host.

### 3.4 How `visibility` interacts with `surface_rule`

**Private wins.** `surface_rule` only shapes *when* a non-private `task` is
rendered. A private task never reaches the daily file, whatever its windows or
`always_visible_if` say: the read gate never hands it to the run, so the
`surface_rule` step never sees it. The `kb-deep-work` "stale task" check skips
it for the same reason. `surface_rule` stays the tool for "not yet" and
`visibility: private` for "never on its own".

## 4. Who reads, and through what

### 4.1 Readers

Every read goes through the engine as one of these readers:

| Reader | Who | Sees |
|---|---|---|
| `owner` | The owner's interactive sessions on the laptop (Claude Code in the vault, `scout-ask`, Scout.app's KB browser, the TUI). In Agnes: the owner's private conversations with their own agent, which nobody else can read. | Everything on disk, private included, labelled. |
| `autonomous` | Any unattended run: briefing, weekend briefing, consolidation, dreaming, research, heartbeat-triggered runs, the enrichment-question generator. In Agnes: unattended agent runs and memory capture. | Non-private notes only. |
| `agent:<principal>` | Another person's agent asking this person's agent (in Agnes: a Chief-of-Staff-to-Chief-of-Staff thread or similar). Also any conversation the owner's agent is in that another person can read. | Nothing while §7's gate is closed. After: notes whose effective `federation_scope` admits the principal. |
| `indexer` | The Agnes indexer reading the synced remote. | Every synced path, with its labels. It does the per-reader filtering on its side, using the same data and rules (§9). |

The rule from the brain-coordinator review applies: **raw vault access is
limited to agents the person owns, in conversations that are theirs alone.**
That is the `owner` reader. A conversation another person can read is never
`owner`, even when the owner's own agent is the one answering.

### 4.2 What each surface honours

| Surface | Reader | `visibility: private` | local-only path | `federation_scope` |
|---|---|---|---|---|
| Owner's interactive session (laptop) | owner | read normally | read normally | ignored |
| Scout.app / TUI KB browser | owner | shown, with a lock badge (P4) | shown | shown as a label |
| Briefing, consolidation, dreaming, research runs | autonomous | not returned by any read command; never quoted, summarised, linked or named in any output | n/a (local) | ignored |
| Outbound text: Slack DM, Telegram, wrap/digest, daily action-items file, review queue, session log, `drafts/`, commit messages of runs | autonomous output | must not contain a private note's content, title, slug or wikilink (§8) | same | ignored |
| Enrichment-question generator | autonomous | skips private files | n/a | ignored |
| Vault git-delta narration | autonomous | commits touching only private paths are dropped; private paths are removed from mixed commits' file lists | n/a | ignored |
| `parser.py query` / `scoutctl kb query` | caller's reader | excluded unless `--include-private`, which only the `owner` reader may pass | — | — |
| `parser.py export` (graph dump) | caller's reader | excluded unless `--include-private` (owner only) | — | — |
| `scoutctl vault sync` (P2 of the Agnes plan) | — | **synced**, labelled | **never pushed**; the host refuses a push containing one | label only |
| Agnes indexer, owner's private sessions | indexer → owner | included | absent (never synced) | label only |
| Agnes unattended runs, memory capture | indexer → autonomous | excluded; memory captured from a note that later turns private is retired | absent | ignored |
| Another person's agent | agent:<p> | never | never | gate closed in v1 (§7); afterwards only notes that admit `<p>` |
| Engine back-port (`scoutctl phases backport`) | — | touches phase/script files, never KB notes; unchanged | — | — |

### 4.3 Local-only paths (answer to Agnes's question 1)

"Never leaves the laptop" stays a **path-level sync setting outside
frontmatter**, not a third `visibility` value. Reasons:

- A push can be refused by path alone, without parsing every note's YAML.
- A missing or misspelled field can't sync a note by accident; a path list
  can't be "forgotten" on a new note in that folder.
- It is a property of where a note lives (personal records, raw scans, the
  inbox), which is how owners already think about it.

An owner who wants one specific note never to leave the laptop moves it into a
local-only folder (or adds its path to the vault's local-only list). It can
also be `private`; the two compose.

The list lives in `privacy.yaml → local_only` (§5) so Scout and the indexer
share one default. Agnes holds the per-person list; before each sync Scout
uses the **union** of Agnes's list and the vault's effective list, so either
side can add a path and neither can drop one the other has.

## 5. Policy as data: `privacy.yaml`

### 5.1 Files

- **Engine default:** `plugin/engine/scout/defaults/privacy.yaml`, the
  human-edited source.
- **Generated snapshot:** `plugin/engine/scout/privacy.snapshot.json`, made
  from it by `python -m scout.scripts.privacy_snapshot`, never hand-edited,
  like `connectors.snapshot.json`. This is what the Agnes indexer loads. It
  carries `schema_version` and the engine version.
- **Vault overlay:** an optional `privacy:` block in the vault's
  `scout-config.yaml`. It can add local-only paths, add `visibility` defaults,
  and lower or raise federation ceilings. The owner edits it; scheduled runs
  may not (§8.3).
- **Effective policy:** `scoutctl kb policy --json` prints engine default +
  overlay, merged. `scoutctl vault sync` writes it to
  `.scout-state/privacy-policy.json` in each pushed commit, so the indexer
  reads the policy that was in force for that commit, as data, without running
  Scout code.

### 5.2 Shape (v1)

```yaml
schema_version: 1

# Paths that never leave the laptop. Gitignore-style globs, vault-relative.
local_only:
  always:            # not overridable by the overlay
    - ".scout-logs/**"
    - ".scout-cache/**"
    - ".claude/**"
    - ".venv/**"
    - ".obsidian/**"
    - ".mcp.json"
    - ".env*"
  default:           # the overlay may add to these; removing one needs the overlay's `local_only_remove:` with a reason
    - "knowledge-base/personal/**"
    - "inbox.md"
    - ".scout-inbox/**"
    - "Raw/**"
    - ".scout-state/**"           # except the allowlist below
  sync_allow:                     # carve-outs from .scout-state/
    - ".scout-state/schedule.yaml"
    - ".scout-state/id-map.json"
    - ".scout-state/privacy-policy.json"

# Path defaults for notes with no `visibility:` key. First match wins.
visibility_defaults: []           # v1 ships none (D6). The overlay may add, e.g.
                                  # - {glob: "knowledge-base/projects/<slug>/**", visibility: private}

# The widest federation_scope a note under this path may have. First match wins.
# A path that matches nothing has ceiling `none`: sharing is opt-in by folder AND by note.
federation_ceilings:
  - {glob: "knowledge-base/topics/**",             ceiling: org}
  - {glob: "knowledge-base/projects/**",           ceiling: org}
  - {glob: "knowledge-base/ontology/entities/**",  ceiling: org}
  - {glob: "knowledge-base/research-queue/**",     ceiling: org}

# Defaults for notes with no `federation_scope:` key. v1: none anywhere (D2).
federation_defaults: []

federation:
  enabled: false     # D9. Engine-side kill switch; only the owner's overlay can flip it, and only after the read-boundary design ships.

# Files that are outbound surfaces of autonomous runs (§8.2).
outbound_surfaces:
  - "action-items/action-items-*.md"
  - "knowledge-base/review-queue*/**"
  - "knowledge-base/review-queue.md"
  - "knowledge-base/session-log/**"
  - "drafts/**"
```

Globs use the same matcher as `kb_lint` (`scout/kb/lint_config.py`). Paths are
compared after resolving the vault's `knowledge_base/` symlink, so a path
can't dodge a rule by its spelling.

### 5.3 Folder lists for Jordan to pick (D6, D7)

The lists above are a proposal. Everything not on the ceiling list is
`none`, which covers the mixed-sensitivity hotspots found in the vault survey:
people files, the daily action-items files and their context notes, source
notes (meeting transcripts with named participants), the review queue, the
mistake audit, session logs, the root indexes and `people.md`. Those mix work,
client and personal content at paragraph level, so only "never" is safe for
them.

`visibility_defaults` ships empty: the owner's personal records already never
leave the laptop, and making `personal/` private would mute the 🐾 Personal
section of the owner's own briefing.

### 5.4 Precedence and inheritance

For a file at path `p` with frontmatter `fm`:

1. `p` matches `local_only.always` or the effective `local_only` list →
   `local_only = true`, effective `federation_scope = none`.
2. `visibility` = `fm.visibility` if present (parsed per §3.1), else the first
   matching `visibility_defaults` entry, else `normal`.
3. `federation_scope` = `fm.federation_scope` if present, else the first
   matching `federation_defaults` entry, else `none`; then capped by the first
   matching `federation_ceilings` entry (no match → `none`); then forced to
   `none` if `visibility = private` or `local_only`.
4. `federation.enabled = false` → every `agent:*` reader is denied, whatever
   step 3 produced. The label still reports the step-3 value.

**No inheritance through links or folders other than via the policy globs.** A
note doesn't become private because it links to, or is linked from, a private
note. A project folder's sibling files and a sharded log's parts don't inherit
from the folder's main file; an owner who wants a whole folder private adds a
`visibility_defaults` glob in the overlay. This keeps the decision a pure
function of (path, frontmatter, policy), which the indexer can reproduce
exactly.

### 5.5 The label

`scoutctl kb classify <path>… --json` and the sync manifest both emit, per
file:

```json
{"path": "knowledge-base/topics/billing/billing.md",
 "visibility": "normal", "visibility_source": "default",
 "federation_scope": "none", "federation_scope_declared": "org",
 "federation_source": "frontmatter", "ceiling": "org",
 "local_only": false, "warnings": []}
```

`*_source` is one of `frontmatter`, `path_default`, `default`, `forced`
(private, local-only or unparseable). The indexer stores the label next to
each document and diffs labels between commits, which is how it finds a note
that has turned private and retires memory captured from it.

## 6. The single enforcement point: `scoutctl kb` read commands

### 6.1 Commands

All take `--reader` and print JSON with `--json`. All resolve the vault the
way `scoutctl kb lint` does.

| Command | Replaces | Does |
|---|---|---|
| `scoutctl kb files [--glob G]…` | `ls`, `find`, `rglob` | Lists the paths the reader may see. |
| `scoutctl kb search <pattern> [--glob G]… [--fixed] [-i]` | `rg` | Runs ripgrep (Python fallback when `rg` is absent) over the reader's visible paths only, never the whole tree then filtered. |
| `scoutctl kb read <path>` | a direct file read | Prints the file, or exits 3 with `{"denied": true, "reason": …}`. A denial never echoes the file's title or content. |
| `scoutctl kb resolve <name>` | `ls knowledge-base/projects/` + guessing | Maps a name, alias or slug to its canonical file, through `name_lookup`, among visible notes only. |
| `scoutctl kb query [filters]` | `parser.py query` | The ontology query with the reader filter. `--include-private` is accepted only for `owner`. |
| `scoutctl kb related <name>` / `traverse` / `path` | `parser.py related` etc. | Graph walks that skip private nodes for non-owner readers: a private node is neither returned nor walked through. |
| `scoutctl kb delta --since <rev\|duration>` | `git log`, `git diff HEAD~1` in phases | The vault's own commits since a point, minus commits whose changed paths are all invisible to the reader, with invisible paths removed from the rest. Diff text only for visible paths. |
| `scoutctl kb classify <path>…` | — | The §5.5 label. Any reader may ask; it reveals labels only for paths the reader can see, and `denied` otherwise. |
| `scoutctl kb policy [--json]` | — | The effective policy. |
| `scoutctl kb check-outbound <file>` | — | Runs the §8.2 `private-mention`/`private-link` matchers over a draft; exit 1 with the offending line numbers (never the matched names) on a hit. |

**One implementation, in the engine** (my call). Today `name_lookup`,
`traverse`, `path` and `export` exist only in the vault's standalone
`parser.py` (rendered from `plugin/templates/knowledge-base/ontology/parser.py`,
which imports nothing from `scout`); the engine's `KnowledgeGraph`
(`scout/kb/ontology.py`) has `query` and `related` only, and its `load()`
skips files without `name:`/`type:`. So P2:

- ports `name_lookup`, `traverse`, `path` and `export` into the engine's
  `KnowledgeGraph`;
- puts the classifier in its own module (`scout/kb/privacy.py`) that walks
  every markdown file, not only entities, so plain notes get labels;
- turns the vault `parser.py` into a thin shim that keeps its CLI and execs
  `scoutctl kb …` with the same arguments. If `scoutctl` can't be found, the
  shim exits non-zero with a message, never falling back to an unfiltered
  read. There is then no third copy of the rules to keep in step: the engine
  is one and the Agnes indexer is the other, both held to the corpus (§9).
  `/scout-update` replaces the rendered file as it does today; a vault that
  has hand-edited its `parser.py` gets the usual proposed-merge sidecar.

### 6.2 Choosing the reader

The reader comes from the environment, and a flag may only **narrow** it:

- `SCOUT_READER` sets the ceiling. The engine-rendered runners
  (`run-scout.sh`, `run-dreaming.sh`, `run-research.sh`) and every engine
  command that launches a run (the heartbeat, `schedule tick`) export
  `SCOUT_READER=autonomous`, next to the `SCOUT_MODE` the runners already
  export.
- **When `SCOUT_READER` is unset** (my call; D11): the reader is
  `autonomous` if `SCOUT_MODE` is set, else `owner`. Every engine-launched run
  sets both, so a scheduled run can't land on `owner` by accident, and the
  owner's interactive tools (`/scout-work`, the `kb-pre-filter`
  UserPromptSubmit hook in an interactive session, hand-run `parser.py`, the
  TUI, Scout.app) keep seeing their own private tasks without each having to
  pass a flag. The alternative, "unset = autonomous", is stricter but makes
  every owner entry point opt back in, and any one it misses silently drops
  the owner's private notes from their own interactive work.
- Vault-local runners the engine doesn't render (for example an extra
  runner some vaults add by hand) set `SCOUT_MODE` already or don't;
  `/scout-update` reports any vault `run-*.sh` that exports neither, as
  drift with the two-line fix.
- `--reader` may narrow (an owner session asking "what would a scheduled run
  see?" passes `--reader autonomous`); it can never widen past
  `SCOUT_READER`.
- A host (Agnes) never relies on the default: it sets
  `SCOUT_READER=autonomous` for unattended runs and
  `SCOUT_READER=agent:<principal>` for a conversation another person can read,
  and §7.1 keeps peer-facing turns off the filesystem in any case.

**What this does and doesn't enforce.** On the laptop, a scheduled run is a
Claude session with shell access to the whole vault. Nothing can stop it from
running `cat` on a private file. The threat there is accidental leakage by a
cooperative model, and the defence is layered: the read commands never hand
it a private note; the phases forbid raw reads of the KB (§6.3); the outbound
checks (§8) catch a slip before it leaves; and the eval canaries (§11) measure
the rate. For **other people's agents** the boundary has to be real, which is
why §7 requires that such a reader never gets a filesystem: the host runs
these commands on its side with the reader fixed, and the agent sees only the
output.

### 6.3 Migration of the raw readers

| Reader today | Raw access | Change |
|---|---|---|
| `scout-ask` skill (#480) | `ls knowledge-base/projects/`, direct reads, fallback `rg`, `parser.py query` | Step 1 uses `scoutctl kb resolve` then `kb files --glob`; the fallback search is `scoutctl kb search`; typed queries use `scoutctl kb query`; reads of a resolved file use `scoutctl kb read` when the reader isn't `owner`. Every command passes `--reader "${SCOUT_READER:-owner}"`. Interactive behaviour is unchanged for the owner. This lands as a follow-up to #480, not inside it. |
| `phases/core/git-setup.md` | `git log --oneline -20`, `git diff HEAD~1`, `git log --since` | `scoutctl kb delta`. Non-KB git work (status, commit, push) is unchanged. |
| `phases/core/action-items.md` (personal tasks, `surface_rule`, `always_visible_if` driver) | `parser.py query --type task` | `scoutctl kb query --type task` (reader from env). |
| `phases/core/kb-management.md`, `phases/modes/kb-deep-work.md` (fact-check pass, stale-task check, shrink target pick) | `git log`/`git diff`, parser, file reads | `kb delta`, `kb query`, `kb files`. `scoutctl kb lint --report` lists only visible files to an autonomous reader. |
| `phases/research/research-targets.md`, `knowledge-integration.md` | entity staleness, `git log` | `kb query`, `kb delta`. A private entity is never a research target, so a run never searches the web for it. |
| `pre_session_data.py` → `.scout-cache/session-context.json` | `rglob`, `parser.py` for personal tasks | Uses the classifier; the cache an autonomous run reads holds no private paths or tasks. |
| `hooks/kb_pre_filter.py` → `.scout-cache/kb-filter.md` | `rglob` with a path skip list | Adds the classifier; private files are left out of the freshness list. |
| `templates/scripts/vault-freshness.py`, `recurring-task-status.py` | `rglob`, frontmatter reads | Skip private files when the reader is `autonomous`. |
| `scout/scripts/heartbeat.py` (`research_queue_has_open`) | direct frontmatter reads of `research-queue/*.md` | Ignores private items, so a private open item never triggers a research run that then finds nothing it may read. |
| The vault-local enrichment-question generator (`scripts/` in the vault, not shipped by the engine) | `rglob` over `people/`, `personal/` | Must call `scoutctl kb files --reader autonomous`. The engine can't edit vault scripts: `/scout-update` reports it as drift with the one-line fix, and the generator is a back-port candidate into `templates/scripts/`. |
| Every phase | ad-hoc `rg`/`cat` of the KB | A new always-included section, `phases/core/privacy.md` (§8.1), says: read the KB only through `scoutctl kb`; never `cat`, `rg`, `grep`, `find` or `ls` inside `knowledge-base/` or `action-items/context/`. |

`scripts/eval.sh` and the eval stubs gain the commands so scenarios run
against the fixture vault.

## 7. The read boundary for other people's agents

### 7.1 What Scout owns and what the host owns

Scout owns the **policy and its evaluation**: the fields, `privacy.yaml`, the
classifier, the read commands, the conformance corpus (§9). The host (Agnes)
owns the **boundary**: who the principal is, which conversations other people
can read, and making sure a peer-facing agent can't reach the vault except
through the policy. The Agnes integration spec cites this section for the
semantics and defines the boundary.

What this spec requires of any host before `federation.enabled` may be turned
on (the gate in D9):

1. **No filesystem for peer-facing turns.** A turn that serves another person
   (or that another person can read) runs without the vault checkout, and
   without the owner's raw index. It gets vault knowledge only from a tool
   that evaluates this policy server-side with the reader fixed to
   `agent:<principal>`.
2. **Fixed principal.** The host, not the model, sets the principal from the
   authenticated asker. A model can't name a different principal.
3. **Answer-time recheck.** An answer built from vault content cites the paths
   it used, and the host rechecks each cited path's label against the asker
   before sending. A citation the asker may not see blocks the answer.
4. **Audit.** Every peer read and every answer is logged (§7.3) and the owner
   can review it.
5. **Revocation.** Narrowing a note's scope, or turning the switch off, takes
   effect on the next read; the host drops cached answers built from it.
6. **Owner consent to turn it on.** The switch lives in the owner's overlay,
   so each person opts in for their own vault. The org can forbid it but
   can't turn it on for them.

Until all six exist, `agent:*` gets nothing, and the indexer stores
`federation_scope` without acting on it.

### 7.2 Consent

- Only the owner sets `visibility` or `federation_scope`, or edits the
  overlay. Scheduled runs may **narrow** (mark something private they judge
  sensitive) only by proposing it in the review queue; they never write
  either field directly. The pre-commit check enforces this for widening
  (§8.3).
- An owner may share a note only with groups they belong to, plus `org`
  (§3.3). The host enforces this at read time as well: a `group:` principal
  naming a group the owner has left admits nobody.
- A run that creates a new note never sets `federation_scope`. New notes are
  `none` by default, so a run can't share anything by writing.
- `/scout-setup` and `/scout-update` explain the two fields in one paragraph
  and point to `scoutctl kb policy`.

### 7.3 Audit

- Local: every `scoutctl kb` call by a non-`owner` reader appends one line to
  `.scout-logs/kb-reads.jsonl`:
  `{ts, reader, command, args_hash, paths_returned, paths_denied, scout_mode}`.
  No content, no query text beyond a hash, so the log itself leaks nothing.
  Owner reads are not logged (they would be most of the volume and protect
  nothing).
- Host: a host that runs the commands server-side writes the same fields to
  its own audit log, plus the asking principal and the answer's cited paths.
- `scoutctl doctor` reports the count of denied reads by autonomous runs in
  the last 7 days. A non-zero count means a phase still reaches for something
  it shouldn't; it is a yellow run-health signal, not an error.

## 8. Outbound guards

The read gate stops a run from *getting* a private note. These stop one from
*sending* something about it anyway (an owner pasted text into a normal note,
or a phase slipped).

### 8.1 Phase rule (`phases/core/privacy.md`)

Included in every mode. It says, in short:

- Read the KB only through `scoutctl kb` (§6.3's last row).
- Never quote, summarise, link, name or count a private note in any outbound
  surface (§4.2), and never mention that a private note exists.
- Never set or widen `visibility` or `federation_scope`; propose narrowing in
  the review queue instead.
- If a command answers `denied`, drop that line of work silently; don't route
  around it with raw reads.

Per `plugin/CLAUDE.md`, this rule gets an eval seed (§11.3).

### 8.2 kb-lint checks

New checks in `scout/kb/lint.py`, run by the pre-commit hook on staged files:

| Check | Fires when | Severity |
|---|---|---|
| `private-link` | A staged file **outside** private notes contains a `[[wikilink]]` that resolves to a private note. | warn (as #453 proposes); **block** when the file is an `outbound_surfaces` match and the commit is from a scheduled run (`SCOUT_MODE` set). |
| `private-mention` | A staged `outbound_surfaces` file contains a private note's slug or `name:` (whole-word, case-insensitive, names of 4+ characters). | warn; block for scheduled-run commits. |
| `visibility-value` | `visibility` isn't `normal`/`private`, or `federation_scope` doesn't parse. | warn |
| `privacy-contradiction` | `visibility: private` with a `federation_scope` other than `none`, or a `federation_scope` above its folder ceiling. | warn |
| `frontmatter-unparseable` | The frontmatter block doesn't parse (the note is now treated as private). | error in report, warn in hook |

Block mode follows the existing `kb_lint.mode` and `SCOUT_LINT_OVERRIDE`
escape hatch, logged as today.

**Scope** (my call): `kb lint --staged` checks only `.md` under
`knowledge-base/` and `action-items/` today (`_SCOPE_PREFIXES` in
`lint_config.py`). The privacy checks run on a wider set: those two trees,
`drafts/**`, and `scout-config.yaml` (for §8.3). The size and diary checks
keep their current scope.

### 8.3 Widening guard, and failing closed

When `SCOUT_MODE` is set (a scheduled run is committing), a staged change
that turns `private` into anything else, removes a `private` line, widens a
`federation_scope`, or touches the `privacy:` block of `scout-config.yaml` is
**blocked** regardless of `kb_lint.mode`. `SCOUT_LINT_OVERRIDE` does not
apply to it. Interactive commits are not checked: the owner may change their
own notes.

The pre-commit hook fails **open** today on purpose (`scout/kb/hook.py`): an
engine error must not stop the owner committing. The privacy checks keep that
for interactive commits but get their own **fail-closed** path when
`SCOUT_MODE` is set: if the privacy checker itself errors, a scheduled run's
commit is refused with a run-health event (`privacy.check_failed`), and the
run's normal retry/park handling applies.

**Commit messages** of scheduled runs are an outbound surface (§4.2) that no
pre-commit hook sees. The engine-managed hook set gains a `commit-msg` hook
that runs the `private-mention` matcher on the message when `SCOUT_MODE` is
set, fail-closed the same way. `git commit --no-verify` bypasses both; the
phase rule forbids it, and the eval check (§11.3) flags it in transcripts.
This is the cooperative layer of §6.2, not a hard boundary.

### 8.4 Sends the engine makes

`scoutctl notify telegram` and any engine-sent message run the
`private-mention` matcher over the outgoing text and drop the message with a
`privacy.outbound_blocked` run-health event if it matches.

Slack DMs (the wrap and digest) are composed and sent by the model through a
connector; no draft file exists today. v1 (my call) adds one step to the
notification section of the phases: write the message to
`.scout-cache/outbound-draft.md`, run `scoutctl kb check-outbound
.scout-cache/outbound-draft.md`, and send only on exit 0; on a hit, drop the
offending lines and re-check. This is cooperative (the model could skip the
step), and the §11.3 eval measures whether runs follow it. A follow-up can
route the DM through a `scoutctl` send command so the engine enforces it
(§12).

## 9. What the Agnes indexer implements (answers to Agnes's questions)

| Agnes question | Answer |
|---|---|
| Exact field names and allowed values | `visibility: normal \| private`; `federation_scope: none \| org \| [person:<email> \| group:<host group id>, …]` (§3, §3.3). Unknown `visibility` → private; unparseable `federation_scope` → none; broken frontmatter → private + none. |
| Default for an unmarked note | `visibility: normal` and `federation_scope: none`, after path defaults (§5.4). Agnes's assumption is correct. |
| Folder defaults: frontmatter or data? | **Data.** Scout never writes defaults into frontmatter; frontmatter is only an explicit override. The indexer loads `.scout-state/privacy-policy.json` from the commit it indexes (or `privacy.snapshot.json` for the engine defaults) and applies §5.4. |
| `visibility: local`? | No. Local-only is path-level (§4.3). The indexer never sees those paths; the host refuses a push that contains one. |

For conformance, the engine ships a contract fixture,
`plugin/engine/tests/fixtures/contract/privacy-corpus.json`: a list of
`{path, frontmatter_text, policy_overlay, expected_label}` cases covering every
rule in §3 and §5.4. It is checksum-guarded like `parser-corpus.json`, and the
Agnes indexer runs the same file in its own tests. A rule change lands as one
commit that edits the corpus, the checksum and the snapshot.

## 10. Rollout

Each phase is one or more PRs; none starts until Jordan approves this spec.
The implementation plan
(`docs/superpowers/plans/2026-10-20-per-note-privacy.md`) breaks them into
tasks.

| Phase | Ships | User-visible change |
|---|---|---|
| P1 Policy + classifier | `privacy.yaml`, snapshot script, `scout/kb/privacy.py`, `kb classify`/`kb policy`, the contract corpus | None yet. |
| P2 Read gate | `kb files/search/read/resolve/query/related/traverse/path/delta/check-outbound`; `name_lookup`/`traverse`/`path`/`export` ported into the engine `KnowledgeGraph`; the vault `parser.py` becomes a shim; `SCOUT_READER` in all engine runners and run launchers; the read log | Private notes vanish from scheduled runs' reads. |
| P3 Phase migration | `phases/core/privacy.md`; git-setup, action-items, kb-management, kb-deep-work and research phases switched to `scoutctl kb`; `pre_session_data`, `kb_pre_filter`, vault scripts; eval seed | Scheduled runs stop raw-reading the KB. Vaults get the phase text through `/scout-update`'s 3-way merge. |
| P4 Guards | the lint checks (wider scope), widening guard, fail-closed path and `commit-msg` hook for scheduled runs, the Slack outbound-draft step, Telegram send guard, doctor count, lock badge in Scout.app | Lint warnings on links into private notes. |
| P5 Vault migration | a bootstrap migration that lists notes with non-standard `visibility:` lines and offers to rewrite each to `private` (owner OK per note, interactive only); drift note for the enrichment generator | The existing ad-hoc markers become real. |
| P6 scout-ask | the §6.3 switch, after #480 merges | None for the owner; safe for other readers. |
| P7 Sync labels | the manifest and `.scout-state/privacy-policy.json` written by `scoutctl vault sync` (that command is its own spec in the Agnes plan) | — |
| Later | turning on `federation.enabled`, gated on §7.1 | Decided separately. |

P1–P4 ship together in one release if review allows; P3 without P2 would point
phases at commands that don't exist.

Performance budget: `kb files` and `kb query` on a 3,000-file vault in under
300 ms warm. The classifier reuses `KnowledgeGraph`'s per-process frontmatter
cache, and the CLI keeps a stat-keyed `.scout-cache/privacy-index.json` so
repeated calls from one run don't reparse every file. `kb search` passes the
visible file list to `rg` (in batches) rather than searching everything and
filtering.

## 11. Test plan

### 11.1 Fixture vault

`plugin/engine/tests/fixtures/privacy-vault/`, anonymised per
`plugin/CLAUDE.md` (owner `Alex`, colleague `Priya`, `example-org/<repo>`,
`PROJ-1234`, generic nouns for vendors). Every private file carries a unique
canary token (`CANARY-PRIV-<n>`, e.g. `CANARY-PRIV-7Q2X`) in its title, body
and slug-adjacent text. Contents:

| File | Purpose |
|---|---|
| `knowledge-base/personal/task-renew-permit.md` | `type: task`, `domain: personal`, `visibility: private`, with a `surface_rule` window covering "today" and an `always_visible_if` that fires — must still never surface. |
| `knowledge-base/personal/task-buy-gift.md` | Normal personal task — must still surface (D6 regression guard). |
| `knowledge-base/people/sam.md` | Person entity, `visibility: private`, with an open question for the enrichment generator. |
| `knowledge-base/people/priya.md` | Normal person linking `[[sam]]`. |
| `knowledge-base/topics/billing/billing.md` | Plain topic, `visibility: private`, no `name:`/`type:`. |
| `knowledge-base/topics/billing/rollout.md` | Normal sibling: no inheritance. |
| `knowledge-base/projects/widget/widget.md` | Normal project, `federation_scope: org`. |
| `knowledge-base/projects/widget/notes.md` | `federation_scope: [person:priya@example.com]`. |
| `knowledge-base/sources/2026-10/2026-10-01-sync.md` | `federation_scope: org` above its `none` ceiling. |
| `knowledge-base/topics/misc/draft.md` | `visibility: PRIVATE while drafting` (fail-closed). |
| `knowledge-base/topics/misc/broken.md` | Unparseable frontmatter. |
| `knowledge-base/topics/misc/plain.md` | No frontmatter. |
| `inbox.md`, `Raw/scan.txt` | Local-only paths. |
| `action-items/action-items-<today>.md` | Seeded with a normal item; the run writes the rest. |
| `scout-config.yaml` | With a `privacy:` overlay adding one `visibility_defaults` glob. |

A test helper builds a git history on top: a commit touching only private
files, a commit touching a private and a normal file, and a normal commit.

### 11.2 Deterministic tests (CI)

- **Classifier, table-driven:** every row of §3 and §5.4, from the contract
  corpus. Checksum test for the corpus.
- **Snapshot:** `privacy.snapshot.json` matches a fresh generation (CI fails on
  drift, like `connectors.snapshot.json`).
- **Read commands, per reader:** for `autonomous` and `agent:person:priya@example.com`,
  `kb files`, `search`, `read`, `resolve`, `query`, `related`, `traverse`,
  `delta` and `classify` over the fixture never print any `CANARY-PRIV-*`
  string or private path; `read` of a private path exits 3 without echoing a
  title. For `owner`, all of them do (interactive unaffected).
- **Reader resolution:** `SCOUT_READER` unset with `SCOUT_MODE` set →
  autonomous; both unset → owner; `--reader owner` ignored when
  `SCOUT_READER=autonomous`; `agent:*` can't be widened. Each engine-rendered
  runner and each run-launching command exports `SCOUT_READER=autonomous`.
- **Kill switch:** `federation_scope: org` notes are denied to `agent:*` while
  `federation.enabled: false`, and returned (only those within ceilings) when
  the fixture overlay sets it true.
- **Ontology:** `KnowledgeGraph.query(type="task", domain="personal")` as
  autonomous omits the private task and keeps the normal one (#453's first
  acceptance line); `related`/`path` don't walk through `sam`.
- **Vault `parser.py`:** same assertions through the rendered template's CLI.
- **`kb delta`:** the private-only commit is absent; the mixed commit lists
  only the normal file; diff text has no canary.
- **`pre_session_data` / `kb_pre_filter` / vault scripts:** generated caches
  contain no canary.
- **Enrichment generator stand-in:** an engine-side test of the reference
  generator (the back-ported template, or a minimal stand-in until then) emits
  no question sourced from `sam.md` (#453's second acceptance line).
- **Lint:** each §8.2 check fires and stays quiet on its negative case; the
  widening guard blocks with `SCOUT_MODE` set and ignores
  `SCOUT_LINT_OVERRIDE`; interactive commits pass.
- **Telegram guard:** a message containing a private note's name is dropped
  and logged.
- **Bootstrap:** `bootstrap install` renders `SCOUT_READER=autonomous` into
  every runner, and `--no-jobs` is used in these tests so no test touches the
  live scheduler.
- **Perf:** `kb files` on a generated 3,000-file vault under the §10 budget
  (marked like the existing perf tests).

### 11.3 Eval (local only, on the maintainer's login)

A new scenario in `plugin/engine/tests/eval/scenarios/` runs a morning
briefing, a consolidation and a dreaming pass over the fixture vault, and a
check in `harness/checks/` greps every output for `CANARY-PRIV-*`: the daily
file, the wrap/digest draft, the Telegram payload, commit messages, the
session-log row and the review queue. Pass = zero hits, and the normal
personal task does appear (#453's third acceptance line). The check also counts
raw `cat`/`rg`/`ls` calls into `knowledge-base/` in the transcript, as the
leading indicator for §6.2's cooperative layer. Model runs are local and
need Jordan's OK, per the no-paid-API rule.

## 12. Non-goals and later work

- **Paragraph-level privacy.** The daily action-items file and context notes
  mix work and personal lines. Their federation ceiling is `none` for that
  reason. Marking a section private inside a note is out of scope.
- **Encryption at rest** of private notes, and admin exclusion on the host.
  Admin exclusion is its own reader-level design on the Agnes side.
- **Automatic classification.** No model stamps `visibility` or
  `federation_scope`. A later design may let dreaming *propose* labels in the
  review queue.
- **Publishing outside the organisation** (`public`, a curated export bundle in
  an open markdown-plus-frontmatter format). A June 2026 design note in the
  maintainer's vault sketched where federation could go: pull-based and
  permissioned (each vault stays its owner's; others read a permitted view,
  never a push or a sync-to-identical copy); grants per peer and per
  capability, scoped by knowledge domain (professional, never personal or
  family); every cross-agent read audited; a curated export as plain markdown
  files in an open format; and the warning not to claim federation before the
  isolation is real. This spec takes the frontmatter field, the
  domain-shaped ceilings, the audit and the "isolation first" gate from it.
  Signed agent identities, capability credentials and the export format are
  later designs.
- **Routing the wrap Slack DM through the engine** so `scoutctl` checks it
  (§8.4).
- **Changing what the owner sees interactively.** Interactive sessions,
  Scout.app and the TUI read everything, as today.

## 13. Open decisions, in priority order

1. **D5 — can scheduled runs read private notes at all?** Recommended: no.
   Consequence to accept: a private note goes stale unless the owner updates
   it interactively, because no run will touch it.
2. **D7 — the federation opt-in folders** (§5.3: topics, projects, ontology
   entities, research queue at ceiling `org`; everything else `none`).
3. **D3/D4 — path-level local-only and policy-as-data.** Recommended as Agnes
   proposed; needs Jordan's yes because Agnes will build on it.
4. **D1 — value names.** `normal`/`private`, and `none`/`org`/principal list.
   The principal forms (`person:<email>`, `group:<host group id>`) are
   agreed with the Agnes spec (§3.3).
5. **D6 — `personal/` stays normal-but-local-only**, not private.
6. **§8.2 severities** — `private-link` warn for the owner's commits, block
   only for scheduled runs' outbound files.
7. **D10 — normalising the existing ad-hoc markers** through a `/scout-update`
   prompt.
8. **D9 — who flips `federation.enabled`**, and that it stays off until the
   host meets all six §7.1 conditions.
9. **D11 — the unset-reader default** (`owner` unless `SCOUT_MODE` is set).
   The stricter "unset = autonomous" is safer for unknown headless callers
   but costs the owner their private notes in any interactive tool that
   forgets the flag.

## 14. Calls made in this spec (check while reading)

- One privacy implementation in the engine; the vault `parser.py` becomes a
  shim over `scoutctl kb` that fails closed if `scoutctl` is missing (§6.1).
- Privacy lint scope widened to `drafts/**` and `scout-config.yaml`;
  scheduled-run commits fail closed on a checker error; a `commit-msg` hook
  checks run commit messages (§8.2–§8.3).
- The Slack wrap/digest guard is a phase step (draft file +
  `kb check-outbound`), cooperative in v1 (§8.4).
- Unset reader resolves via `SCOUT_MODE` (D11).
- Read log records hashes and paths, never content; owner reads aren't
  logged (§7.3).
- No inheritance through links or folders except policy globs (§5.4).
- Graph walks don't pass through private nodes for non-owner readers (§6.1).
