# The Lab: research as Scout's own lines of inquiry

Status: **draft for Jordan's review, 2026-11-21.** Replaces the app's Research tab and the
`knowledge-base/research-queue/` model. Touches the research brain (`plugin/phases/research/`),
the vault format, `scoutctl`, and the macOS app.

## 1. Why

Research runs are fully autonomous: Scout picks what to look into, reads, and writes it up.
The Research tab doesn't show that. It is the Wishlist screen pointed at
`knowledge-base/research-queue/`, a folder of one-file "items" with **Start / Done / Drop**
buttons. Those buttons make sense for work the owner does. Here the owner never does the
work, so they make no sense.

Three problems, as the owner put it:

1. **A Start button for work Scout does.** The controls treat research as the owner's to-do
   list.
2. **You can't tell what a topic is working on or what it found.** A card is a markdown body
   (one grew to 230 KB) plus a History pane of commit subjects and diffs.
3. **No way to steer.** The only feedback channel is replying to a run's wrap message. Nothing
   in the app captures a comment, and nothing lets you steer one part of a topic.

The queue has also drifted away from where research actually lands. On the maintainer's vault,
189 of 199 queue files are `done`, the 5 "active" ones are lanes the runs now skip, and the
runs write their real output as topic notes under `knowledge-base/topics/`, which the Research
tab never shows.

The new model is an **autonomous lab**. Scout follows a small number of **lines of inquiry**.
Each one has a driving question, a set of open questions, the evidence gathered so far, and
small **experiments** Scout plans and runs on data it can safely touch. The owner reads it,
approves which lines exist, and steers by commenting on a whole line or a single question.
Structurally it sits in the knowledge base. It is markdown in the vault, readable in Obsidian
and explained by git history. The app gives it a view built for following progress.

## 2. Goals, non-goals, anti-goals

**Goals**

1. Every line of inquiry ("thread") shows, at a glance:
   - its driving question
   - what is understood so far
   - its **open questions**
   - its **progress**: questions answered, open, newly raised
   - a **timeline** of what each run did in it
2. The owner can comment on a thread or on **one specific open question**. The next research
   run reads the comment, acts on it, and replies under it. That is the granular steer.
3. Scout runs **empirical experiments** when reading isn't enough. They are planned first and
   visible before they run (§6.3).
4. Scout **proposes** threads. The owner approves, edits or rejects each one. Only approved
   threads get research time.
5. The lab is KB-native: plain markdown with `[[wikilinks]]` under `knowledge-base/`.

**Non-goals**

- A general project tracker, or a replacement for action items. Research findings that need
  owner action still go through the research brief and the next consolidation, as today.
- Changing *what* a vault researches. Scope rules such as a vault's own `RESEARCH.md` scope
  restriction stay where they are and constrain which threads Scout may propose.
- Real-time progress. The lab updates when a research run commits.

**Anti-goals** (failure even if it technically works)

- **Controls that imply the owner does the work.** There are no Start, Done or "run now"
  buttons. The owner's verbs are *approve, reject, pause, resume, close, veto, comment*.
- **Another graveyard.** If most threads sit untouched for weeks, or the list fills with closed
  threads, the lab has failed the same way the queue did. Closed threads collapse out of the
  way, and Scout proposes closing a thread with nothing left to ask.
- **An incomprehensible timeline.** A timeline entry is a sentence a cold reader understands
  (what was read or tested, what it showed, which questions moved). It is never a commit
  subject or a diff.
- **Experiments with side effects.** An experiment that posts, sends, writes outside its own
  folder, or changes how Scout works is a bug.
- **Comments that vanish.** A comment that no run answers is the worst outcome here: it teaches
  the owner that steering doesn't work.

**Success looks like:** the owner opens a thread and in under a minute can say what it's
asking, what is known, and what happens next. A comment left before a run gets a visible
reply, and a visible change in the thread, after that run.

## 3. Definitions

| Term | Meaning |
|---|---|
| **Thread** | One line of inquiry. It runs for weeks, has one driving question, and lives in one folder. |
| **Open question** | A sub-question inside a thread, with a stable ID (`Q1`, `Q2`, …) that never changes or gets reused. Status: `open`, `answered`, `dropped`. |
| **Timeline entry** | One entry per research run that touched the thread: what was done, what it found, which questions moved. |
| **Experiment** | An empirical test Scout runs itself, on its own data, with a plan and a cost cap. ID `E1`, `E2`, … within the thread. |
| **Evidence** | The notes a thread draws on: topic notes, sources, experiment results. |
| **Steer** | An owner comment on a thread or on one question. |

## 4. Approaches considered

**A. Threads as markdown folders in the KB (chosen).** One folder per thread: a front page with
fixed headings, an append-only `timeline.md`, one note per experiment. Comments are indented
lines in the Action Items comment convention. `scoutctl lab …` does the writes and a lint check
keeps the structure parseable.
*Trade-off:* the app parses structure out of markdown, which is fragile. Fixed headings,
fixed line formats and a pre-commit lint address that.
*Why chosen:* it is the only option that gives questions, experiments and comments a real home
while staying KB-native.

**B. Prose in markdown, structure in YAML/JSONL sidecars** (questions, timeline and comments as
data files written only through `scoutctl`).
*Trade-off:* sturdier parsing and concurrent writes, but half the lab is unreadable in Obsidian
and it moves away from "like the KB".
*Rejected* for that reason.

**C. Derive threads from existing topic notes** (add a `thread:` tag and build the timeline
from research commits).
*Trade-off:* the smallest change, but open questions and planned experiments have nowhere to
live. A commit-derived timeline is also exactly what today's History pane does, which is part
of what made it incomprehensible.
*Rejected.*

## 5. Vault format

```
knowledge-base/lab/
  lab.md                          # index: threads by status, links (never index.md)
  <thread-slug>/
    <thread-slug>.md              # front page
    <thread-slug>-timeline.md     # newest first, appended by runs
    experiments/
      <thread-slug>-E1-<short>.md
      <thread-slug>-E1-<short>/   # scripts, small outputs (optional)
```

Every file name carries the thread slug. Bare `[[wikilinks]]` resolve by file name across the
whole vault, and `kb-move` refuses duplicate names, so a `timeline.md` in every folder would
make links ambiguous. Throughout this doc, "the timeline" means `<thread-slug>-timeline.md`, and
`E2-…` is shorthand for `<thread-slug>-E2-<short>.md`.

Topic notes stay where they are (`knowledge-base/topics/…`) and are linked from a thread's
Evidence section. A note can serve several threads. A note's frontmatter may carry
`threads: [slug, …]` as a back-link. The thread's Evidence list is the source of truth.

### 5.1 Front page

```markdown
---
type: lab-thread
title: Keeping cached summaries honest
status: active            # proposed | active | paused | closed | rejected
created: 2026-11-21
approved: 2026-11-22      # set when the owner approves
last_run: 2026-11-24T01:11
visibility: normal        # normal | private (same rules as other KB notes)
next_q: 8                 # next question ID to issue
next_e: 3                 # next experiment ID to issue
---
# Keeping cached summaries honest

## Question
What is the cheapest signal that a stored summary no longer matches the page it summarises?

## Current understanding
Short prose, rewritten in place each run (not appended), cited with [[wikilinks]].
Budget: about 2 KB; detail belongs in the evidence notes.

## Open questions
- **Q3** Does a hash of the cited sections flag stale summaries better than a changed-length rule? · open · raised 2026-11-20
  - alex: try it on a real sample of pages, not only synthetic ones
  - scout (2026-11-21 01:11): Planned [[summary-drift-E2-section-hash|E2]] for the next run.
- **Q5** … · open · raised 2026-11-21

## Answered questions
- **Q1** Is changed length alone a usable staleness signal? · answered 2026-11-20 — no: it missed rewrites
  that kept a page the same size; see [[summary-drift-notes]].
- **Q2** … · dropped 2026-11-21 — owner: not relevant

## Experiments
- [[summary-drift-E2-section-hash|E2]] Section-hash staleness check on sampled pages · planned · runs after 2026-11-21 13:00
- [[E1-…|E1]] … · done 2026-11-20 — result in one line

## Evidence
- [[summary-drift-notes]] — one line on what it contributes
- [[regeneration-cost-notes]] — …

## Comments
- alex: thread-level comment
  - scout (2026-11-21 01:11): reply
```

Format rules (enforced by lint, §9):

- The headings above, in this order. A section may be empty, but it can't be missing or
  renamed.
- **Question lines:** `- **Q<n>** <text> · <status> · <date phrase>`, optionally followed by
  ` — <one-line answer or reason>`. IDs are unique within the thread and never reused.
- **Comment lines:** indented `  - <handle>: <text>`, the same handle rule as Action Items
  (`CommentAuthor.handle`). Scout's replies use `scout (<timestamp>): <text>`, nested under the
  comment they answer. A comment belongs to the question (or thread) it sits under.
- A question moves from Open to Answered by moving its line, comments included.

### 5.2 Timeline

`timeline.md`, newest entry first. A run adds an entry and never rewrites old ones.

```markdown
### 2026-11-21 01:11 · research run
- **Did:** went through the rewritten-pages export; drafted E2.
- **Found:** the length rule disagrees with a manual check on most rewrites; the section-hash check is untested.
- **Questions:** opened Q5 · answered Q1 · Q3 still open (E2 planned)
- **Steers handled:** alex on Q3 → planned E2
- Evidence: [[regeneration-cost-notes]]
```

Allowed tags after the time: `research run`, `experiment`, `owner` (approve, pause, close,
veto, and edits made through the app), `proposal`. When the file passes the log budget it is
sharded with the existing `kb-shard-log` mechanism, as session logs are.

### 5.3 Experiment note

```markdown
---
type: lab-experiment
id: E2
thread: summary-drift
questions: [Q3]
status: planned            # planned | running | done | abandoned | vetoed
planned: 2026-11-21T01:11
not_before: 2026-11-21T13:00
cost_cap_usd: 1.00
data: [sampled pages 2026-10-01..2026-11-20]
# optional, §6.4: timeout_hours, models, disk_budget_bytes
---
## Hypothesis
## Method
## Data
## Result            # filled when done: numbers, plus what was *not* shown
## What it means     # one paragraph, back to the question(s)
```

Scripts and small outputs go in the sibling `E2-<slug>/` folder, so the result can be rerun.
Large outputs and anything derived from private data follow the existing KB privacy and size
rules. An experiment over private data gets `visibility: private`, and so does any note built
from it.

## 6. Lifecycle

### 6.1 Threads

```
proposed ──approve──▶ active ◀──resume── paused
   │                    │  └──pause──▶ paused
   └──reject──▶ rejected   └──close──▶ closed
```

- **Scout proposes.** A proposal is a thread folder with `status: proposed`, a Question, 2–5
  starting open questions, and Evidence if it gathers existing notes. Scout proposes when:
  - an active thread raises a question big enough to stand alone
  - the lab has fewer active threads than `lab.target_threads` (default 5, my call)
  - or for a new vault with an empty lab, from the entity scoring that today's Step 1b does
  A run may make at most **2 proposals**, and Scout keeps at most **5 proposals open** at once
  (my calls), so proposals can't pile up.
- **Only the owner approves, rejects, pauses, resumes, closes or reopens.** These are app
  buttons, or `scoutctl lab <verb>`. Each one appends an `owner` timeline entry.
- **Editing a proposal** means editing its front page (the Question, the starting questions)
  before approving. In the app this opens the KB editor on the front page.
- **Scout may propose closing** a thread once it has no open questions, or after
  `lab.stale_runs` (default 6, my call) runs without movement. The proposal is a thread-level
  `scout` comment plus a badge. The owner closes it, or comments to keep it going.
- **Inside an active thread, Scout may add, answer and drop questions on its own.** Dropping a
  question the owner raised or commented on needs the owner's comment first.

### 6.2 Steering (comments)

- A comment can be thread-level or attached to a question. The owner can also raise a new
  question by writing a thread-level comment starting `Q:`. Scout turns it into a question with
  the next ID and replies with that ID.
- **Unanswered owner comments are the top priority of the next research run** (§7), across all
  active and paused threads. A paused thread's comments are answered, but they don't resume
  research.
- Every owner comment gets a `scout (<ts>):` reply in the run that handles it, saying what was
  done: planned an experiment, answered, re-prioritised, declined with a reason. If a run hits
  its budget before reaching a comment, the comment stays unanswered and is first in line next
  run. The wrap message says how many comments are still waiting.
- A comment is *answered* when it has a nested `scout` reply. That is the whole rule, so the
  app and the engine can both compute it.

### 6.3 Experiments: plan, then run

- **Scout plans** an experiment by writing its note with `status: planned` and
  `not_before` = planning time + `lab.experiment_notice_hours` (default 6 h, my call). The
  experiment can then run in the next research run at the earliest. On a twice-daily schedule
  that leaves the owner most of a day to object.
- **The owner can veto** with the app's Veto button (`status: vetoed`, plus an `owner` timeline
  entry), or with a comment on the experiment line. Before running, a run treats any
  unanswered owner comment on the experiment's line, or on the questions it targets, as a hold.
  It answers the comment first and runs the experiment only if the comment doesn't object.
- **Running it:**
  - It happens inside an ordinary research run, under the existing run write sandbox.
  - At most `lab.experiments_per_run` (default 1, my call) per run.
  - Spending stops at `cost_cap_usd`, which defaults to `lab.experiment_cost_cap_usd`
    (default $1.00, my call). An experiment that would exceed its cap stops and records a
    partial result.
- **In bounds:**
  - reading the vault (KB, transcripts under the vault's session data, `.scout-logs`, the
    mistake audit, connector call logs)
  - running local scripts
  - making model calls within the cap
  - writing only inside the experiment's note and its sibling folder
- **Out of bounds:**
  - posting or sending anything
  - writing to connectors or external systems
  - editing any other vault file
  - changing Scout's own instructions, config or code
  - downloading or executing third-party code (model weights fetched owner-side under §6.4 are
    data, not code, and never reach the script by download)
  - pulling private material into web or connector queries
- **Enforced for scripts, not only instructed** (Jordan, 2026-11-21). An experiment's scripts run
  only through `scoutctl lab experiment run <thread> <E-id> -- <cmd>`, which starts them inside a
  narrower OS sandbox profile built on the run write sandbox (`plugin/engine/scout/sandbox/`).
  Under it a script can:
  - **write** only inside its own experiment folder (plus a scratch temp dir); every other path,
    the rest of the vault included, is refused by the kernel
  - **read** the vault, but not private-visibility files unless the experiment is itself
    `visibility: private`
  - **reach the network** only through the metered model-call wrapper below; any other network
    access is denied
  - **not launch** `scoutctl` write commands, git, or agent CLIs other than the wrapper's
    `claude -p`

  Scout's own steps around an experiment (planning it, writing the note, reading results) stay
  governed by the research instructions, like the rest of a research run. A refused write or
  connection is recorded in the experiment's Result as a finding, and the run doesn't retry it
  another way.
- **What the cost cap covers:** the model calls the experiment itself makes, not the run's own
  reasoning about it, which counts toward the run's normal budget. Those calls are `claude -p`
  subprocesses launched through `scoutctl lab experiment call`. It adds up each call's reported
  `total_cost_usd` in the experiment folder and refuses new calls once the cap is reached, so the
  experiment records a partial result. Nothing meters part of a run today, which is why the cap
  attaches to the subprocesses (my call).
- **When a result suggests changing Scout**, it goes to the existing research→Scout path (the
  research findings log, and the Changes map once that is live), not into a Scout edit.
- **The result is written even when it is negative or inconclusive.** "Showed nothing" is a
  result, scoped by method and date as the research brain already requires for negatives.

### 6.4 ML experiments: long runs, model weights, the GPU and disk

Added 2026-11-25. The owner's interpretability work trains and probes small models with PyTorch
on the Mac's GPU. Four limits in §6.3 and §14a stopped that: a 540 s run, no way to get weights,
no GPU, and 512 MiB of disk. Jordan decided each one (2026-11-23):

| Limit | Decision |
|---|---|
| An experiment must finish within one run (540 s) | **Detached runs**: a run starts it, a later run collects it |
| No network, so no model weights | **An owner-side weights cache**: fetched outside the sandbox, read-only to scripts |
| The GPU isn't usable in the sandbox | **Allow MPS**: open only what PyTorch's Metal backend needs, proven by a kernel test |
| 512 MiB per experiment | **A per-plan byte budget**, capped by the owner, with a free-disk floor |

Everything else in §6.3 stays: plan, then run, after the notice; the veto; the cost cap; writes
only in the experiment's folder; no network for the script but the meter; and the kernel, not
the instructions, holding the script to it.

All new limits are the owner's, in the run broker's `config.json` `lab` block, beside
`experiments_enabled` (§14a). A run can't write them. The engine defaults keep a fresh install
where it is today: GPU off, no weights fetched, no detached runs, 512 MiB. The defaults below
marked "Jordan's" are the values for the maintainer's own config.

**Detached runs.**
- `scoutctl lab experiment run <slug> <EID> --detach -- <cmd>` makes the same broker checks as an
  attached run: the owner's switch, the per-run allowance, the notice, the veto, the commit and
  the argv. Then the broker starts a **supervisor** outside every sandbox, in a session of its own,
  and returns once the supervisor has written its state record. It doesn't wait for the script:
  a weights fetch may come first (below), and a run can't wait more than 10 minutes. The status
  line is `experiment started: detached`. A gate check that fails later is the experiment's
  outcome, `not run`, read by the next collect. The experiment stays `running` and the run goes on with its
  other work.
- The supervisor does what the attached runner does today: the profile, the gate and its kernel
  checks, the meter, the process, byte and file limits, the kill sweep and the run log. Its
  deadline is the plan's `timeout_hours`, clamped to the owner's `lab.detached_max_hours`
  (engine default 0, meaning off; Jordan's 12).
- It writes a state record in the owner folder (`lab-runs/<vault hash>-<slug>-<EID>.json`):
  started, deadline and its pid; the sweep's probe path once the script's gate opens; then the
  outcome. A run can't write it.
- **Collecting.** A later run calls `scoutctl lab experiment collect <slug> <EID>`, which asks
  the broker for the record and ends with one status line:
  - `experiment ran: exit <n>` → the run finishes the experiment, as after an attached run;
  - `experiment running: <elapsed> of <deadline>` → leave it, and say so in the wrap;
  - `experiment not run: <reason>` → the supervisor is gone without an outcome (a restart, a
    kill). The collect first sweeps that run's sandbox, using the probe path in the record, so
    nothing the script started outlives it. The run then abandons the experiment with the reason.
- **One at a time.** At most `lab.max_detached` (engine default 0; Jordan's 1) run at once per
  vault. A detached start counts toward the run's `experiments_per_run`.
- **The owner can stop one.** A veto of a `running` experiment stops it: the supervisor checks
  the owner's veto store at each poll and kills the run. `scoutctl lab experiment stop` does the
  same without vetoing. Both record `stopped by the owner` as the outcome.
- **Sleep and restarts.** The deadline is wall-clock time, so a Mac that sleeps uses up the
  experiment's time. A restart or logout ends the supervisor, and the next collect reports it.
- Short experiments still run attached, within `--timeout 540`.

**The weights cache.**
- **The plan names the weights.** An experiment note's frontmatter may list
  `models: [{repo: <owner>/<name>, revision: <40-hex commit>}]`. The revision must be a full
  commit hash, so the weights that run are the ones the owner could see during the notice.
- **Fetching is owner-side and happens when the experiment starts.** The supervisor fetches
  missing weights before it opens the gate, outside any sandbox, from the Hugging Face Hub only.
  That is why an experiment that names models must run detached. The fetch:
  - takes only files with an allowlisted suffix: `.safetensors`, `.json`, `.txt`, `.model`,
    `.tiktoken`. Never `.py`, `.bin`, `.pt`, `.pth`, `.pkl` or `.ckpt`: pickled weights run code
    when loaded. A repo that has no safetensors weights is refused, and so is one whose config
    asks for `trust_remote_code`;
  - uses the owner's Hugging Face login when one exists. The script never sees that token. Gated
    models need the owner to accept their licence on the Hub first. A refused fetch ends the
    experiment `not run`, with the reason;
  - checks the free disk first (below) and refuses when the files would cross the floor.
- **The cache** is a folder in the owner's cache (`~/Library/Caches/Scout/lab-weights/<repo>/<revision>/`),
  capped at `lab.weights_max_bytes` (engine default 0, meaning no fetching; Jordan's 40 GiB).
  When full it drops the least recently used revision that no `planned` or `running` experiment
  names; if that isn't enough, the fetch is refused.
- **Scripts read it, never write it.** The profile allows the cache folder back to read, and the
  script gets `$SCOUT_LAB_WEIGHTS_DIR`. Writes there stay denied, as they are everywhere outside
  the experiment's folder.
- **The record.** The run log lists each model's repo, revision and every file's sha256, and the
  Result cites them.
- **The Python environment is the owner's.** The Lab installs no packages. The owner names an
  interpreter in `lab.python` (a venv with PyTorch, made once by hand), and `python3` in an
  experiment's argv means that interpreter when it is set. Its folder is allowed back to read.

**The GPU.**
- **What building it found (2026-11-25, ruling P2-R11).** PyTorch's MPS backend already works
  under the experiment profile as built. The profile allows by default, and none of its denies
  touches the GPU. The fixed probe (`tests/fixtures/lab/mps_probe.py`: a tensor on `mps`, a
  512×512 matmul, a synchronize) printed `mps ok` with no rule added.
- **So the owner's switch closes the GPU instead of opening it.**
  - With `lab.gpu` false (the engine default), the profile ends with one deny:
    `(deny iokit-open-user-client (iokit-user-client-class "AGXDeviceUserClient"))`. That is the
    GPU's IOKit user client, and torch then reports MPS unavailable.
  - With `lab.gpu: true` (Jordan's), nothing is added: the profile is the one built before the
    switch existed, byte for byte.
- **Metal tries three things the sandbox refuses**, with the switch on or off:
  - a write to its shared shader cache;
  - a write to MPSGraph's per-process temp folder;
  - a read of `~/.CFUserTextEncoding`.

  None of them stops the GPU. No environment variable points either cache into the scratch dir:
  both ignore `TMPDIR`, and Metal reads no variable that names a cache folder. So all three stay
  denied, and shaders recompile on each run. §14b records each rule with how it was found.
- Kernel tests prove it:
  - the probe prints `mps ok` with the switch on, and finds no MPS with it off;
  - with the switch on, a write outside the folder, a direct TCP connection and a read of a
    private file are each still refused.
- **Memory.** On Apple silicon the GPU shares the Mac's memory, and the sandbox has no memory
  limit today. The supervisor reads each process's physical footprint at every poll (it includes
  GPU allocations) and kills the run past `lab.max_memory_bytes`. The default is half the Mac's
  memory (my call).

**Disk.**
- **A per-plan budget.** An experiment note may set `disk_budget_bytes`. The broker clamps it to
  the owner's `lab.max_total_bytes` (engine default 512 MiB; Jordan's 10 GiB). With none set, the
  budget is 512 MiB. Bytes count as they do today: the folder, the scratch dir and unlinked open
  files.
- **A free-disk floor.** An experiment doesn't start, and a fetch doesn't begin, unless the
  vault's volume would still have `lab.disk_free_floor_bytes` free after the budget (or the
  fetch) is spent. The engine default is 15 GB (my call), and the reply is
  `experiment not run: free disk would fall below the floor`. While running, the supervisor kills
  the run if free space drops below the floor, whatever caused the drop.
- **Checkpoints never go into git.** Scripts write large files into `checkpoints/` in their
  folder. The vault's `.gitignore` excludes `knowledge-base/lab/*/experiments/*/checkpoints/`,
  and the broker's commit verb refuses any file over 10 MiB under `knowledge-base/lab/`. The
  Result lists the checkpoints by path, size and sha256. Checkpoints stay on disk until the owner
  deletes them; the research wrap reports how many bytes they hold.

## 7. Research run changes (engine: `plugin/phases/research/`)

`research-targets.md` is rewritten around the lab. The order of work in a run:

1. **Answer owner comments**: every unanswered owner comment in active or paused threads,
   oldest first.
2. **Run due experiments**: `planned`, past `not_before`, not held (§6.3).
3. **Advance active threads**, picked by:
   - owner-prioritised first (a thread-level comment like "focus here" counts, until Scout's
     reply says it's been handled)
   - then the longest since `last_run`
   - skipping any thread whose subject is out of scope for the vault
   A run works **1–2 threads in depth**, keeping the current "depth over breadth" guidance.
   Working a thread means:
   - read its front page
   - pick the open question(s) where a reading pass or an experiment would move things most
   - research or plan an experiment
   - write or update topic notes as today
   - then update the thread: Current understanding, question moves, Evidence, timeline entry,
     `last_run`
4. **Propose threads** if the conditions in §6.1 hold.
5. **Commit and notify**: one timeline entry per touched thread; the wrap message lists each
   touched thread in one line ("Q3 now has a planned experiment; Q1 answered"), any
   proposals awaiting approval, and any comments still waiting.

What is removed:

- the `research-queue/` item model and its `urgent`-priority preemption
- the graph-completeness ranking rule
- the run-log continuity note

What is kept, re-expressed for threads:

- **Coverage mandates** (`scripts/coverage-mandates.py`, when a vault has it) become "advance
  this thread first". A mandate with no matching thread becomes a proposal.
- **Owner-urgent** becomes a thread-level owner comment asking for focus.

Steps 2a–2c (gather, research, verify), the privacy rules, the negative-claim rule and
Phase 4 commit/notify stay as they are.

**No research outside threads** (my call). Today's Step 1b entity enrichment (people,
organisations, projects with thin context) only feeds proposals. A vault that wants it approves
a standing thread such as "Keep key entities current", whose open questions are the thin
entities. On a vault with no approved threads, a run only answers comments and proposes. The
migration (§10) produces the first proposals in the first run, so this never leaves a vault idle
for long.

**Heartbeat gating.** `heartbeat.py` fires research today when the queue has an open item. It
fires instead when any of these hold:

- an active thread exists
- an owner comment is unanswered
- an experiment is due
- fewer than 5 proposals are open, so a proposal is possible

**Concurrent writes.** Runs and the app both write lab files **only through `scoutctl lab`**
(runs included: `question add|answer|drop`, `understanding set`, `evidence add`,
`timeline add`, `experiment plan|start|finish`). Each command:

- takes a short lock on `.scout-state/lab.lock` for the length of that one write
- re-reads the file, applies one targeted change, and commits

An owner comment or pause made mid-run is therefore never overwritten. A run handles comments
it saw when it read the `lab` block. One committed after that point waits for the next run,
unless the run reads that thread again before it finishes. A pause takes effect at the run's
next write to that thread: the command refuses research writes to a paused thread, and comment
replies are still allowed.

The session context's `research_queue` block is replaced by a `lab` block:

- threads by status, each with: open, answered and dropped counts; unanswered-comment count;
  due experiments; `last_run`
- the proposals awaiting approval

A run picks from that block and reads only the threads it works, the same discipline the queue
summary enforces today.

## 8. App: the Lab tab replaces Research

The sidebar row becomes **Lab** (my call on the name; the tab subtitle says "Scout's research").
It is a dedicated view, not `PerFileListView`. The Changes map design already retires the
Wishlist tab, so this removes the last user of the per-file list.

**List (left / main):** threads grouped by status.

- **Needs you:** proposals, close-proposals, experiments planned but not yet past `not_before`
- **Active**
- **Paused**
- **Closed and rejected:** collapsed

Each row shows:

- the title and the driving question (one line)
- progress, e.g. "3 of 7 open · 1 raised this week"
- the last run date
- a ⚗ marker when an experiment is planned or running
- a 💬 marker when Scout replied to the owner since the owner last opened the thread

**Thread page:**

```
Keeping cached summaries honest                             Active · 9 runs · last Nov 21 01:11
[Pause] [Close]                                           (proposed: [Approve] [Edit] [Reject])

Question        What is the cheapest signal that a summary is stale?
Understanding   rendered prose with links into the KB tab

Open questions (3 of 7 open)
  Q3  Does a section hash beat the length rule …?        ⚗ E2 planned   💬 2   [Comment]
  Q5  …                                                                       [Comment]
  ▸ Answered (3) · Dropped (1)

Experiments
  E2  Section-hash staleness check on sampled pages — planned, runs after Nov 21 13:00
      cap $1.00 · targets Q3                                             [Veto] [Comment]
  E1  … — done Nov 20: one-line result                                         [Open]

Timeline (newest first)
  Nov 21 01:11  research run — drafted E2 · opened Q5 · answered Q1
  Nov 20 14:09  owner — approved thread
  ▸ older

Evidence        linked notes, each opening in the KB tab
Comments        thread-level thread + composer
```

- Comments reuse `CommentComposerView` / `CommentListView` and the `CommentAuthor` handle, so
  the byline matches Action Items.
- The prose sections and experiment notes render with the KB reader, so wikilinks, privacy
  labels and links work as they do in the KB tab.
- **All writes go through `scoutctl lab …`** (approve, reject, pause, resume, close, reopen,
  veto, comment). The engine owns the file format and the lint, and the app never edits the
  front page's structure directly (my call; it mirrors how Action Items comments go through
  `add_comment`). Each write is one commit and registers with Edit ▸ Undo the way per-file
  writes do now.
- **Sidebar badge** = proposals + close-proposals + Scout replies the owner hasn't opened. It
  counts only things that are the owner's to look at.
- The lab folder also shows in the KB tab's tree like any other KB folder.

## 9. Validation

`scoutctl lab lint`, run by the vault's kb-lint pre-commit hook on any staged file under
`knowledge-base/lab/`. It checks:

- the front-page headings and their order
- the question line format, unique IDs, and that `next_q`/`next_e` are above every ID used
- the status vocabularies
- that every Q/E ID a timeline entry or experiment mentions exists in its thread
- experiment frontmatter: required fields, `not_before` ≥ `planned`
- that `lab.md` lists every thread folder

A violation blocks the commit with a message naming the line, as other KB lint failures do. The
app's parser is tolerant: it shows an unparseable section as raw markdown with a "doesn't match
the lab format" note, rather than hiding it.

## 10. Migration (one time, through `scout-update`)

1. **Retire the queue.** `knowledge-base/research-queue/` stays on disk as history and is
   linked from `lab.md` under "Before the lab". Runs stop reading it.
   `research-queue.md.tmpl`, the session-context `research_queue` block, and the
   `migrate_perfile`, `kb_pre_filter` and `heartbeat` references move to the lab equivalents.
2. **Open queue items become proposals.** Each `open` / `in-progress` queue item becomes a
   `proposed` thread: its title becomes the Question, its body is summarised into starting
   questions, and it links back to the old file. The owner approves or rejects each one. Items
   whose subject is out of scope for the vault are listed in `lab.md` and not proposed.
3. **Existing research notes are grouped into threads.** The first research run after the
   upgrade groups topic notes written by research runs into **6–8 proposed threads**. Each
   proposal lists the notes it takes in and starts with the open questions those notes leave
   unresolved. Notes no proposal takes in stay in the KB as plain reference. This takes the
   whole run (no new research that run), so the owner gets a reviewable batch at once.
4. **The maintainer's vault mirrors these changes in its own `RESEARCH.md`**, keeping its local
   scope rule.

## 11. Privacy

Thread and experiment `visibility` follows the KB rules:

- A private thread is never a web or connector query target and never named on a shared
  surface, the same as private queue items today.
- Proposals built from private material start private.
- Wrap messages name only normal-visibility threads.
- Experiments over private data stay local and private.
- The lab reads `visibility` with the privacy layer's own parser: any value but `normal`
  (`Private`, `secret`, a broken frontmatter block) is private; an absent key is `normal`.
- An experiment's folder (`knowledge-base/lab/*/experiments/*/`: scripts, outputs, run logs) is
  private by path, forced in the shipped privacy policy whatever a file's own frontmatter
  says (an owner overlay can't change it). The experiment's note beside it keeps
  its own label. A script reads and writes its own folder only; every other experiment's folder
  and the vault's `.scout-state/` (the run broker's outgoing queue) are unreadable to it.
- `lab show --json` and `lab list --json` leave out, for a reader who doesn't see everything,
  each experiment whose note that reader can't see; `lab show` counts them in
  `private_experiments`, as `lab.md` counts private threads.

## 12. Build order

Each step is its own PR, in this order:

1. **Format and tools.** `scoutctl lab` (read, list, all owner verbs, comment, lint), the
   pre-commit lint wiring, and the `lab` session-context block. It also includes the
   experiment sandbox profile behind `scoutctl lab experiment run` and the metered `call`
   wrapper, with tests showing that a write outside the folder, a direct network connection and
   an over-cap call are each refused. Tests run on fixture threads.
2. **The research brain.** First the §6.4 engine work: detached runs, the weights cache, the GPU
   rules and the disk budget, each with kernel tests. Then rewrite `research-targets.md` and
   adjust `knowledge-integration.md` and `commit-notify.md` for timeline entries, steers,
   experiments (attached and detached) and proposals; then `_assemble` verification.
3. **The app's Lab tab.** List, thread page, comments, owner verbs through `scoutctl`, badge.
   Remove the Research per-file tab.
4. **Migration.** `scout-update` step plus the one-time grouping run.
5. **Mirror to the maintainer's vault**, then watch the first week. The success check: comments
   answered in the next run, and at least one experiment planned and run end to end.

## 13. Decided

By Jordan (2026-11-21):

- A topic is a **line of inquiry**, not a single note. Expect 5–10 active at a time.
- **Scout proposes threads; Jordan approves** (or edits or rejects) each one.
- Each thread shows **open questions, progress and a timeline**, and takes **comments on the
  thread or on a single question**.
- Experiments are **plan, then run**: visible as planned before they run, vetoable, never
  waiting on approval.
- Experiments never touch anything outside Scout.
- That limit is **enforced for experiment scripts by an OS sandbox**, not only by instructions.
- **Approach A**: markdown thread folders in the KB.

By Jordan (2026-11-23), for ML experiments (§6.4):

- **Detached runs** for experiments longer than one run allows.
- **An owner-side weights cache**, fetched outside the sandbox and read-only to scripts.
- **Allow MPS**, with only the access PyTorch's Metal backend needs.
- **A per-plan byte budget** under an owner maximum, with a free-disk floor.

My calls in §6.4 (flagged for review):

- weights pinned to a full commit hash, safetensors only, Hugging Face Hub only
- an experiment that names models must run detached, because the fetch happens at start
- the owner's Python environment named in `lab.python`, made by hand; the Lab installs nothing
- a memory limit (half the Mac's memory by default), because the GPU shares it
- a 15 GB free-disk floor, a 40 GiB weights cache, at most 1 detached run at a time, 12 h at most
- checkpoints in `checkpoints/`, never committed; a 10 MiB per-file commit limit under the lab
- a veto of a running experiment stops it
- engine defaults leave all four off, so only an owner's config turns them on

My calls (flagged for review):

- the "Lab" name
- the defaults: 5 target threads; 2 proposals per run and 5 open; 6 h notice; 1 experiment
  per run; $1.00 cap; close-proposal after 6 runs without movement
- all app writes going through `scoutctl lab`
- the `Q:` comment shorthand for raising a question
- the migration's one grouping run that does no new research
- a 2 KB budget for Current understanding
- slug-prefixed file names; the cost cap metering `claude -p` subprocesses
- no research outside threads, with entity enrichment as an optional standing thread
- every lab write, runs' included, going through `scoutctl lab` with a short lock

## 14a. Found while planning (2026-11-21, Plan 1)

These follow from how the engine works today. They change how the spec is built, not what it
does.

- **Nested sandboxes don't work.** A scheduled run already executes inside `sandbox-exec`, and
  macOS refuses a second one inside it (`sandbox_apply: Operation not permitted`). So the run's
  **broker** (`scout/sandbox/broker.py`, outside the sandbox) runs experiment scripts, through a
  new `experiment` verb. A scheduled run with no broker can't run experiments. It refuses and
  records "not run".
- **Experiment limits are the owner's, not the vault's.** The broker reads the per-run count and
  the maximum cost cap from the owner's run-broker `config.json`, the file that already holds
  the Telegram limits, which a run can't write. It clamps a note's `cost_cap_usd` to that
  maximum.
- **The plan-then-run notice is enforced by the broker, from git.** Before running an
  experiment, it checks two things in commits only it can make:
  - the experiment note was committed at least `experiment_notice_hours` ago
  - its status at HEAD is not `vetoed`
  So a run can't backdate `not_before`.
- **Scripts can reach the network only through a meter.** That is a unix socket the runner
  serves outside the sandbox. It runs each `claude -p` call, adds up the cost, and refuses calls
  once the cap is reached. A single call can overshoot by its own cost.
- **`scoutctl lab` writes files but doesn't commit.** Inside a run, commits go through the
  broker's `commit` verb (`git-safe-commit.sh`), as every other run write does. The app commits
  its own owner writes, as it does today. The lock still makes each write safe.
- **The app reads the lab through `scoutctl lab list --json` / `show --json`** instead of
  parsing markdown itself, so there is one parser (the engine's). Its JSON carries any parse
  problems, which the app shows as "doesn't match the lab format" (§9). The JSON is a contract
  with canonical fixtures, like the changes map's.

## 14b. The GPU rules (filled in while building §6.4)

Filled in 2026-11-25 under ruling P2-R11. MPS needed no rule opened: under the profile as built,
the probe printed `mps ok` with nothing added. Below are the one rule the owner's switch adds, and
the three accesses Metal tries that stay denied. A rule not listed here is not in the profile.

**How they were found.** The probe (`tests/fixtures/lab/mps_probe.py`) ran through
`runner.run_experiment` on a fixture vault, with `python3` as the owner's torch venv
(torch 2.6.0, macOS 26, Apple silicon). Then the sandbox's denial log was read:
`log show --predicate 'sender == "Sandbox"'`, filtered to the probe's pid. A second run added
reporting allows (`(allow iokit-open (with report))`, and a reporting mach-lookup allow placed
before the existing mach denies so they still won). That run showed what the GPU itself uses:
`iokit-open-user-client AGXDeviceUserClient` and `IOSurfaceRootUserClient`, and
`mach-lookup com.apple.MTLCompilerService`. None of these is a file, network, exec or denied
service rule.

| Rule | Operation and filter/path | Why | Found by | Profile |
|---|---|---|---|---|
| GPU off | `iokit-open-user-client`, `(iokit-user-client-class "AGXDeviceUserClient")` | The only switch found that turns MPS off cleanly: torch reports MPS unavailable, and the probe exits 2. Denying `mach-lookup` of `com.apple.MTLCompilerService` instead crashes MPS, with an assertion in `MPSKernelDAG.mm`. The class is Apple silicon's; on another Mac the deny matches nothing. | The reporting run showed the GPU opened through this class; the probe was then re-run with the deny. | **Denied** when `lab.gpu` is false (`scout.lab.sandbox.GPU_OFF_RULES`); not added when true. |
| Metal shader cache | `file-write-data`, `/private/var/folders/<user>/C/com.apple.metal/<id>/{libraries,functions}.list` (`DARWIN_USER_CACHE_DIR`) | Metal caches compiled shaders there, in one folder shared by every Metal app of the owner's. Refused, it recompiles: slower, not failing. No env var moves it (it ignores `TMPDIR`; the only shader-cache variable in the system libraries is `MTL_SHADER_CACHE_SIZE`). | Denial log, `deny(1) file-write-data …/C/com.apple.metal/…` | **Stays denied** (the write deny of `/`). |
| MPSGraph temp folder | `file-write-create`, `/private/var/folders/<user>/T/com.apple.MetalPerformanceShadersGraph/mpsgraph-<pid>-<date>-<n>` (`DARWIN_USER_TEMP_DIR`) | MPSGraph makes a per-process folder in the shared temp dir. Refused, it prints "Error creating directory" on stderr and carries on. No env var moves it (it ignores `TMPDIR`; the `MPSGRAPH_DUMP_*_PATH` variables are for debug dumps). | Denial log, `deny(1) file-write-create …/T/com.apple.MetalPerformanceShadersGraph/…` | **Stays denied** (the write deny of `/`). |
| CoreFoundation text encoding | `file-read-data`, `~/.CFUserTextEncoding` | CoreFoundation reads the owner's text-encoding setting there. Refused, it uses the default encoding. | Denial log, `deny(1) file-read-data /Users/<owner>/.CFUserTextEncoding` | **Stays denied** (the home read deny, R32). |

## 14. Left to the builder

- Exact `scoutctl lab` subcommand and flag names, and the JSON shape of the `lab`
  session-context block.
- How the app records "owner has seen this reply" (a local per-thread last-opened timestamp is
  enough).
- Whether `timeline.md` sharding reuses `kb-shard-log.py` as-is or needs a thread-aware
  wrapper.
- Visual details of the Lab view within the existing design system: decide in the running app,
  not from mockups.
- §6.4: how the weights fetch talks to the Hub. Plain HTTPS against its documented file API is
  preferred to adding `huggingface_hub` as an engine dependency.
- §6.4: how the supervisor reads a process's physical footprint (`proc_pid_rusage` is one way).
