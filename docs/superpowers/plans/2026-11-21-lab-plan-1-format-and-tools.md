# The Lab, Plan 1: format, `scoutctl lab`, lint, and the experiment sandbox

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the engine everything the Lab needs before any run or app uses it:
- the thread file format, with a parser and writer
- the `scoutctl lab` commands for owner verbs, run verbs and comments
- the `lab-format` lint check
- the `lab` session-context block and JSON contract
- sandboxed, metered experiments launched by the run broker

**Architecture:** A new package `plugin/engine/scout/lab/` has four layers:
- pure text transforms over the markdown files
- locked file operations
- JSON summaries
- an experiment runner, with its sandbox profile and model-call meter

`scoutctl lab` wraps the operations. The broker gains an `experiment` verb, because a run is
already inside `sandbox-exec` and can't start a second sandbox. Nothing in this plan changes
the research instructions or the app (Plans 2 and 3).

**Tech Stack:**
- Python 3.12, typer, pytest
- macOS `sandbox-exec` (SBPL)
- existing engine modules: `scout.safe_write`, `scout.config`, `scout.kb.lint`,
  `scout.kb.read.visibility_map`, `scout.sandbox.profile`, `scout.sandbox.broker`

**Spec:** `docs/superpowers/specs/2026-11-21-research-lab-design.md`. Read §5, §6, §9 and §14a
before starting. §14a overrides earlier sections where they differ.

## Global Constraints

- **Setup.** Work in the worktree `~/.scout-worktrees/research-lab-design` on branch
  `docs/research-lab-design`. Make a venv in `plugin/engine`:
  `python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'`. All commands below run from
  `plugin/engine`.
- **Fixtures are anonymized** (`plugin/CLAUDE.md`):
  - people `Alex` / `Priya` / `Sam`, comment author `alex`
  - no real names, companies or channels
- **Times.** Every time written to a lab file uses `scout.config.now(data_dir)` (the vault's
  zone), formatted `YYYY-MM-DD HH:MM`. Never bare `datetime.now()` in lab code. Tests inject
  `now=`.
- **`scoutctl lab` never commits.** Callers commit: the broker inside runs, the app for owner
  writes.
- **Vault naming.** No file is named `index.md`. Every lab file name starts with its thread slug,
  except `lab.md`.
- **Slugs and IDs:**
  - slug `^[a-z0-9][a-z0-9-]{0,63}$`
  - question ID `Q<n>`, experiment ID `E<n>`, where n is 1–9999
  - IDs are never reused
- **Front-page headings**, in order, exactly: `Question`, `Current understanding`,
  `Open questions`, `Answered questions`, `Experiments`, `Evidence`, `Comments`.
- **Status vocabularies:**
  - thread: `proposed active paused closed rejected`
  - question: `open answered dropped`
  - experiment: `planned running done abandoned vetoed`
  - timeline tags: `research run`, `experiment`, `owner`, `proposal`
- **Defaults:**
  - in `scout/defaults/scout-config.yaml` under `lab:`: `target_threads: 5`,
    `max_proposals_per_run: 2`, `max_open_proposals: 5`, `stale_runs: 6`,
    `experiment_notice_hours: 6`, `experiment_cost_cap_usd: 1.00`
  - owner limits in the run-broker `config.json` under `"lab"`: `experiments_per_run: 1`,
    `max_cost_cap_usd: 1.00`, `notice_hours: 6`, `timeout_s: 1800`
- **Errors.** Add `LabError(ScoutError)` with `exit_code = 2` to `scout/errors.py`, documented
  like `ActionItemError`. The CLI's existing handler prints it.
- **Platform.** Sandbox and broker tests that need the kernel skip unless
  `scout.sandbox.profile.available()`.
- **Gates.** `ruff check`, `ruff format --check` and `mypy scout/lab` pass at every commit.
  Commit messages are `feat(lab): …` / `test(lab): …`.

## Review Focus

1. **Owner comment text carrying the format's own tokens.** That covers ` · `, ` — `,
   `**Q3**`, a leading `Q:`, a newline, or `scout (…):`. It must land as one comment line that
   reparses to the same text and never becomes a question line or a Scout reply. Test in
   Task 2.
2. **A front page hand-edited in Obsidian with a heading renamed or missing.** `show --json`
   still returns every section it can read, plus a problem naming the line. Every write to that
   thread refuses with a message naming the heading, and never rewrites the file. Tests in
   Tasks 1 and 3.
3. **Two writers on one thread at the same moment** (the app's comment and a run's question
   answer). Both changes land. Test in Task 3.
4. **A private thread, or private KB files.** Delegate readers' `lab list` omits private
   threads. A normal-visibility experiment's script can't read a private file. Tests in Tasks 6
   and 10.
5. **An experiment script that hangs or forks a child that outlives it.** The runner kills the
   whole process group at the timeout and reports `timed_out`. Nothing it started keeps
   running, and the experiment's status is left for the run to finish or abandon. Test in
   Task 10.

---

## File structure

```
plugin/engine/scout/lab/
  __init__.py
  model.py        dataclasses + vocabularies + regexes (no I/O)
  parser.py       markdown → model, tolerant, collects Problems
  writer.py       pure text transforms (str → str), raise LabError on unusable input
  paths.py        where each lab file lives
  ops.py          lock + read/transform/atomic-write; owner verbs; run verbs; proposals; lab.md
  experiments.py  plan/start/finish/abandon/veto, holds
  summary.py      list/show JSON (schema_version 1)
  lint.py         lab-format findings for kb lint
  meter.py        model-call meter server + client
  sandbox.py      the experiment SBPL profile
  runner.py       run one experiment under the profile with a meter
  cli.py          `scoutctl lab …`
plugin/engine/tests/unit/
  test_lab_parser.py  test_lab_writer.py  test_lab_ops.py  test_lab_experiments.py
  test_lab_summary.py  test_lab_contract.py  test_lab_lint.py  test_lab_meter.py
  test_lab_sandbox.py  test_lab_runner.py  test_lab_cli.py  test_run_broker_experiment.py
plugin/engine/tests/fixtures/lab/            hand-written fixture threads (anonymized)
plugin/engine/tests/fixtures/contract/lab-list.json, lab-thread.json   generated
Modify: scout/errors.py, scout/cli.py, scout/kb/lint.py, scout/kb/lint_config.py,
        scout/scripts/session_context_kb.py, scout/scripts/pre_session_data.py,
        scout/sandbox/broker.py, scout/defaults/scout-config.yaml
```

---

### Task 1: Model and tolerant parser

**Files:**
- Create: `scout/lab/__init__.py`, `scout/lab/model.py`, `scout/lab/paths.py`, `scout/lab/parser.py`
- Create: `tests/fixtures/lab/summary-drift/summary-drift.md`, `tests/fixtures/lab/summary-drift/summary-drift-timeline.md`,
  `tests/fixtures/lab/summary-drift/experiments/summary-drift-E1-baseline.md`. These are the spec's §5.1–§5.3
  examples, anonymized: the comment author is `alex`; questions Q1 (answered), Q2 (dropped),
  Q3 and Q5 (open). Q3 has an `alex` comment with a `scout` reply, plus an unanswered `alex`
  comment. E1 is done, and E2 is planned and listed on the front page.
- Modify: `scout/errors.py` (add `LabError`)
- Test: `tests/unit/test_lab_parser.py`

**Interfaces:**
- Produces, in `model.py`:
  - `THREAD_STATUSES`, `QUESTION_STATUSES`, `EXPERIMENT_STATUSES`, `TIMELINE_TAGS`,
    `FRONT_HEADINGS` (tuples, values from Global Constraints)
  - `SLUG_RE`, `QID_RE = r"Q[1-9]\d{0,3}"`, `EID_RE = r"E[1-9]\d{0,3}"`
  - `@dataclass Comment(author: str, text: str, stamp: str | None, line: int, replies: list[Comment])`,
    with property `is_scout: bool` (`author == "scout"`)
  - `@dataclass Question(qid: str, text: str, status: str, when: str, note: str | None, line: int, comments: list[Comment])`
  - `@dataclass ExperimentRef(eid: str, target: str, title: str, status: str, when: str, note: str | None, line: int, comments: list[Comment])`
  - `@dataclass EvidenceRef(target: str, why: str, line: int)`
  - `@dataclass Problem(line: int | None, message: str)`
  - `@dataclass Thread(slug: str, frontmatter: dict[str, Any], title: str, question: str, understanding: str, open: list[Question], answered: list[Question], experiments: list[ExperimentRef], evidence: list[EvidenceRef], comments: list[Comment], problems: list[Problem])`,
    with properties `status: str` and `visibility: str` (default `"normal"`), and method
    `unanswered_owner_comments() -> list[tuple[str, Comment]]` returning `(target, comment)`.
    `target` is `"thread"`, a QID or an EID. A comment counts as unanswered when it is an owner
    comment (`not is_scout`) with no reply where `is_scout` is true.
  - `@dataclass TimelineEntry(stamp: str, tag: str, bullets: list[str])`
  - `@dataclass Experiment(slug: str, eid: str, frontmatter: dict[str, Any], status: str, questions: list[str], sections: dict[str, str], problems: list[Problem])`
- Produces, in `paths.py`:
  - `lab_dir(vault: Path) -> Path` (`vault/"knowledge-base"/"lab"`)
  - `index_path(vault) -> Path` (`lab/lab.md`)
  - `thread_dir(vault, slug) -> Path`
  - `front_page(vault, slug) -> Path`
  - `timeline_path(vault, slug) -> Path`
  - `experiment_note(vault, slug, eid) -> Path | None`, which globs `experiments/<slug>-<eid>-*.md`
    and returns None when absent
  - `experiment_dir(note: Path) -> Path` (the sibling folder, same stem)
- Produces, in `parser.py`:
  - `parse_front_page(slug: str, text: str) -> Thread`
  - `parse_timeline(text: str) -> list[TimelineEntry]` (newest first, as written)
  - `parse_experiment(slug: str, text: str) -> Experiment`
  - `load_thread(vault: Path, slug: str) -> Thread`
- Line grammar the parser accepts. Every writer in Task 2 emits exactly this.
  - question: `- **<QID>** <text> · <status> · <when>`, optionally followed by ` — <note>`.
    Split from the right on ` · `, so the text is everything before the last two ` · `
    separators. `<when>` runs up to an optional ` — `.
  - experiment line: `- [[<target>|<EID>]] <title> · <status> · <when>`, optionally followed by
    ` — <note>`
  - evidence: `- [[<target>]] — <why>` or `- [[<target>|<alias>]] — <why>`
  - comment: `<indent>- <handle>[ (<stamp>)]: <text>`, where handle is `[A-Za-z][A-Za-z0-9._-]*`.
    Depth is relative to the line it hangs under:
    - under a question or experiment line, a comment is indented 2 spaces and a reply 4
    - in `## Comments`, a comment is indented 0 and a reply 2

- [ ] **Step 1: Write failing tests** in `test_lab_parser.py`:
  - `test_parses_fixture_thread`: `status == "active"`; `[q.qid for q in t.open] == ["Q3", "Q5"]`;
    `[q.status for q in t.answered] == ["answered", "dropped"]`; `t.open[0].comments[0].author == "alex"`;
    `t.open[0].comments[0].replies[0].is_scout`; `t.experiments[1].status == "planned"`;
    `t.problems == []`
  - `test_unanswered_owner_comments`: returns exactly `[("Q3", <the second alex comment>)]`
  - `test_question_text_may_contain_middle_dot`: `- **Q9** a · b · open · raised 2026-11-21`
    parses to `text == "a · b"`, `status == "open"`
  - `test_renamed_heading_is_a_problem_not_a_crash`: with `## Open Qs` in place of
    `## Open questions`, `parse_front_page` returns `open == []`, a problem whose message names
    `Open questions`, and the other sections intact
  - `test_unknown_status_is_a_problem`: `· pending ·` gives a problem; the question is kept with
    `status == "pending"`
  - `test_parse_timeline_fixture`: 2 entries, the first has `tag == "research run"`, bullets
    start `**Did:**`
  - `test_parse_experiment_fixture`: `status == "done"`, `questions == ["Q1"]`, `"Result" in sections`
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_lab_parser.py -q`. Expected:
  every test fails, with `ModuleNotFoundError: scout.lab`.
- [ ] **Step 3: Implement** `model.py`, `paths.py`, `parser.py` and `LabError`.
  - Frontmatter via `scout.kb.frontmatter`'s existing split helper. Use whichever function that
    module exposes for "frontmatter dict + body"; don't write a new YAML reader.
  - Sections are found by `## ` headings.
  - Unknown lines inside a section become problems, never exceptions.
- [ ] **Step 4: Run** the same command. Expected: all pass.
- [ ] **Step 5: Commit**
  `git add scout/lab scout/errors.py tests/unit/test_lab_parser.py tests/fixtures/lab && git commit -m "feat(lab): thread model and tolerant parser"`

### Task 2: Pure writers

**Files:**
- Create: `scout/lab/writer.py`
- Test: `tests/unit/test_lab_writer.py`

**Interfaces:**
- Consumes: Task 1 parser, model, regexes.
- Produces. Every function takes and returns the whole front-page text, and raises `LabError`
  when the section it needs is missing or the target ID is absent. The error message names the
  heading or ID.
  - `set_frontmatter(text: str, **fields: str | int) -> str` (adds or replaces keys, keeps order
    and the other keys)
  - `add_comment(text: str, target: str, author: str, body: str, stamp: str) -> str`.
    `target` is `"thread"`, a QID or an EID. It appends after the target's last existing comment
    or reply.
  - `add_reply(text: str, target: str, index: int, body: str, stamp: str) -> str`.
    `index` is 1-based among the target's top-level comments, and the author is always `scout`.
  - `add_question(text: str, qtext: str, raised: str, by: str | None = None) -> tuple[str, str]`.
    It returns `(text, qid)`, takes the ID from `next_q` and increments it, and writes
    `raised <raised>` (plus ` by <by>` when given) as `<when>`.
  - `answer_question(text: str, qid: str, note: str, day: str) -> str`. It moves the line and
    its comments to `## Answered questions` (newest first), with status `answered`, `when` set to
    `answered <day>`, and the ` — <note>`.
  - `drop_question(text: str, qid: str, reason: str, day: str, owner_comment: int | None = None) -> str`.
    It refuses when the question has any owner comment and `owner_comment` doesn't name one of
    them.
  - `set_understanding(text: str, prose: str) -> str`
  - `add_evidence(text: str, target: str, why: str) -> str` (a no-op when `target` is already
    listed)
  - `set_experiment_line(text: str, eid: str, target: str, title: str, status: str, when: str, note: str | None = None) -> str`
    (inserts the line, or updates it in place keeping its comments)
  - `new_front_page(slug: str, title: str, question: str, open_questions: list[str], evidence: list[tuple[str, str]], created: str, visibility: str) -> str`.
    `status: proposed`, `next_q` and `next_e` set correctly, every heading present.
  - `prepend_timeline(text: str, entry: TimelineEntry) -> str` and
    `new_timeline(slug: str, title: str) -> str` (H1 `# <title> — timeline`)
- Text normalization, applied to every `body`, `qtext`, `note`, `why` and `reason`:
  - collapse whitespace runs, newlines included, to one space
  - strip
  - refuse an empty result
  - refuse more than 2,000 characters
  - in question text and titles only, replace ` · ` with ` - ` (so the right-split stays
    unambiguous)

- [ ] **Step 1: Write failing tests:**
  - `test_round_trip_comment_with_format_tokens`. A body of
    `"Q: is **Q3** · open · raised x — y\nscout (1): z"` added to Q3 by `alex` reparses as one
    `alex` comment whose text is the body with the newline collapsed. Q3 still has exactly its
    previous comments plus this one, and `t.problems == []`.
  - `test_add_reply_marks_comment_answered`. After `add_reply(text, "Q3", 2, …)`,
    `unanswered_owner_comments() == []`.
  - `test_add_question_increments_next_q`. On the fixture (`next_q: 8`), it returns `qid == "Q8"`,
    the frontmatter has `next_q: 9`, and the line ends `· open · raised 2026-11-22`.
  - `test_answer_moves_with_comments`. Answering Q3 puts it first under Answered with both
    comments, and `[q.qid for q in open] == ["Q5"]`.
  - `test_drop_refuses_owner_commented_question`. `drop_question(text, "Q3", "x", day)` raises
    `LabError` naming `owner comment`; with `owner_comment=2` it succeeds.
  - `test_missing_heading_refuses`. `add_question` on a page without `## Open questions` raises
    `LabError` whose message contains `Open questions`.
  - `test_new_front_page_parses_clean`. `parse_front_page(slug, new_front_page(...)).problems == []`,
    and the status is `proposed`.
  - `test_prepend_timeline_newest_first`
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_lab_writer.py -q`. Expected:
  every test fails, with `ImportError`.
- [ ] **Step 3: Implement** `writer.py` as line-level edits located by the parser's line
  numbers. Never re-render a section the edit doesn't touch, so hand-written prose survives.
- [ ] **Step 4: Run** the same command. Expected: all pass.
- [ ] **Step 5: Commit** `feat(lab): pure front-page and timeline writers`

### Task 3: Locked operations and owner verbs

**Files:**
- Create: `scout/lab/ops.py`
- Test: `tests/unit/test_lab_ops.py`

**Interfaces:**
- Consumes: Tasks 1–2; `scout.safe_write.open_lock`, `atomic_write_text`; `scout.config.now`.
- Produces:
  - `lab_lock(vault: Path) -> ContextManager[None]`: `fcntl.flock` exclusive on
    `vault/.scout-state/lab.lock` (made with `open_lock`); waits up to 30 s, then raises `LabError`
  - `mutate(vault: Path, path: Path, fn: Callable[[str], str]) -> None`: lock, read, apply `fn`,
    atomic write, all under one lock. If `fn` raises, nothing is written.
  - `stamp(vault: Path, now: datetime | None = None) -> str` (`YYYY-MM-DD HH:MM` in the vault's
    zone)
  - `OWNER_TRANSITIONS: dict[str, tuple[frozenset[str], str]]`:
    - `approve`: proposed → active
    - `reject`: proposed → rejected
    - `pause`: active → paused
    - `resume`: paused → active
    - `close`: {active, paused} → closed
    - `reopen`: {closed, rejected} → active
  - `owner_verb(vault, slug, verb, author, reason=None, now=None) -> Thread`:
    - checks the transition (otherwise `LabError` "cannot <verb> a <status> thread")
    - sets `status`, plus `approved: <day>` on approve
    - prepends an `owner` timeline entry with the bullet `**<Verb>:** by <author>[ — <reason>]`
    - writes both files under one lock
  - `comment(vault, slug, target, author, body, now=None) -> None`: allowed in every status
    except `rejected`
  - `reply(vault, slug, target, index, body, now=None) -> None`
  - `require_active(thread: Thread) -> None`: raises
    `LabError("<slug> is <status>; research writes need an active thread")`

- [ ] **Step 1: Write failing tests.** Copy the fixture thread into `tmp_path/knowledge-base/lab/`
  first.
  - `test_approve_proposed`: the front page becomes `status: active` with an `approved:` date,
    and the timeline's first entry is tagged `owner` with `**Approve:** by alex`
  - `test_illegal_transition`: `owner_verb(..., "resume", ...)` on an active thread raises
    `LabError`, and both files are byte-identical afterwards
  - `test_comment_on_paused_allowed_rejected_refused`
  - `test_write_refuses_on_broken_heading`: after the fixture's `## Open questions` is renamed,
    `comment(..., "Q3", ...)` raises and the file is unchanged
  - `test_concurrent_writers_both_land`: two `multiprocessing` processes, one calling
    `comment(..., "thread", "alex", "one")` 20 times and one calling `add_evidence` through
    `mutate` 20 times. The result has 20 comments and 20 evidence lines.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_lab_ops.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `ops.py`.
- [ ] **Step 4: Run** the same command. Expected: all pass.
- [ ] **Step 5: Commit** `feat(lab): locked writes and owner verbs`

### Task 4: Run verbs, proposals and the lab index

**Files:**
- Modify: `scout/lab/ops.py`
- Modify: `scout/defaults/scout-config.yaml` (add the `lab:` block from Global Constraints)
- Test: `tests/unit/test_lab_ops.py` (extend)

**Interfaces:**
- Produces in `ops.py`. Every run verb calls `require_active` first.
  - `propose(vault, slug, title, question, open_questions: list[str], evidence: list[tuple[str, str]], visibility="normal", now=None) -> Thread`.
    It refuses an existing slug, and refuses when the count of `proposed` threads is
    ≥ `lab.max_open_proposals`. It creates the front page, a timeline with a `proposal` entry
    (`**Proposed:** <question>`), and the index entry.
  - `question_add(vault, slug, qtext, by=None, now=None) -> str` (returns the QID)
  - `question_answer(vault, slug, qid, note, now=None)`
  - `question_drop(vault, slug, qid, reason, owner_comment=None, now=None)`
  - `understanding_set(vault, slug, prose)`
  - `evidence_add(vault, slug, target, why)`
  - `timeline_add(vault, slug, tag, did, found, questions, steers=None, evidence=None, now=None)`.
    The bullets are `**Did:**`, `**Found:**`, `**Questions:**`, then `**Steers handled:**` and
    `Evidence:` when given. `tag == "research run"` also sets `last_run` in the front matter.
  - `write_index(vault) -> None`: rewrites `lab.md` from the thread folders. Headings
    `## Needs you` (proposed), `## Active`, `## Paused`, `## Closed` (closed + rejected); one line
    each `- [[<slug>]] — <question>`; a `## Before the lab` section kept verbatim if present.
    `propose` and `owner_verb` call it.

- [ ] **Step 1: Write failing tests:**
  - `test_propose_creates_three_files_and_index`: the front page parses clean with
    `status == "proposed"`, `lab.md` lists it under `## Needs you`, and the timeline's first tag
    is `proposal`
  - `test_propose_refuses_sixth_open_proposal`
  - `test_research_write_refused_on_paused`: on a paused thread `question_add` raises, while
    `reply` succeeds
  - `test_timeline_research_run_sets_last_run`
  - `test_write_index_keeps_before_the_lab`
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_lab_ops.py -q`. Expected: the
  new tests fail.
- [ ] **Step 3: Implement.** Read config through `scout.config.load_config(vault)["lab"]`.
- [ ] **Step 4: Run** the same command. Expected: all pass.
- [ ] **Step 5: Commit** `feat(lab): proposals, run verbs and the lab index`

### Task 5: Experiment lifecycle

**Files:**
- Create: `scout/lab/experiments.py`
- Test: `tests/unit/test_lab_experiments.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `plan(vault, slug, short, title, questions: list[str], body_md: str, cap_usd: float | None = None, now=None) -> Experiment`.
    - Requires an active thread, every listed question open, `short` matching `^[a-z0-9-]{1,32}$`,
      and `body_md` containing `## Hypothesis`, `## Method` and `## Data`.
    - Takes the EID from `next_e`.
    - Writes `experiments/<slug>-<EID>-<short>.md` with frontmatter `type: lab-experiment`, `id`,
      `thread`, `questions`, `status: planned`, `planned`, `not_before` (planned +
      `lab.experiment_notice_hours`), `cost_cap_usd` (the cap, or `lab.experiment_cost_cap_usd`),
      and `visibility` (the thread's).
    - Appends `## Result` and `## What it means`, empty.
    - Sets the front-page line `planned · runs after <not_before>`.
  - `holds(thread: Thread, exp: Experiment) -> list[Comment]`: unanswered owner comments on the
    EID line or on any question in `exp.questions`
  - `start(vault, slug, eid, now=None) -> Experiment`: planned → running. It refuses before
    `not_before` ("not before <time>"), when `holds` is non-empty ("held by <n> comment(s)"), and
    when the status is not `planned`. It sets `started`.
  - `finish(vault, slug, eid, result_md, means_md, one_line, now=None) -> Experiment`:
    running → done. It fills the two sections and sets the front-page line
    `done <day> — <one_line>`.
  - `abandon(vault, slug, eid, reason, now=None)`: {planned, running} → abandoned
  - `veto(vault, slug, eid, author, now=None)`: planned → vetoed, with an `owner` timeline entry
    `**Veto:** <EID> by <author>`

- [ ] **Step 1: Write failing tests:**
  - `test_plan_sets_notice_and_cap_from_config`: with `now = 2026-11-21 01:11`, `not_before` is
    `2026-11-21 07:11`, the cap is `1.0`, and the front-page line contains
    `planned · runs after 2026-11-21 07:11`
  - `test_start_refuses_before_not_before`
  - `test_start_refuses_when_held`: add an `alex` comment on the E2 line, and `start` raises
    `held by 1 comment`
  - `test_start_then_finish`: the note has `status: done`, the Result text is present, and the
    front-page line ends `— <one_line>`
  - `test_veto_only_from_planned`
  - `test_plan_refuses_answered_question`
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_lab_experiments.py -q`.
  Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: all pass.
- [ ] **Step 5: Commit** `feat(lab): experiment lifecycle with notice and holds`

### Task 6: JSON summaries, contract fixtures, session context

**Files:**
- Create: `scout/lab/summary.py`
- Create: `tests/unit/test_lab_summary.py`, `tests/unit/test_lab_contract.py`
- Generated: `tests/fixtures/contract/lab-list.json`, `tests/fixtures/contract/lab-thread.json`
- Modify: `scout/scripts/session_context_kb.py` (add `lab_block`, `LAB_MAX_BYTES = 16_000`,
  a `("lab", LAB_MAX_BYTES, _trim_lab)` row in the budget table, `"lab"` in the key order list
  next to `"research_queue"`)
- Modify: `scout/scripts/pre_session_data.py` (a `lab` field on the context dataclass, filled
  next to `research_queue` with the same try/except-to-empty pattern)
- Modify: the root `CLAUDE.md` (a bullet beside "The changes map has two canonical copies"
  naming the two lab fixtures and the regenerate command)

**Interfaces:**
- Produces:
  - `SCHEMA_VERSION = 1`
  - `lab_list(vault: Path, labels: Mapping[str, str] | None, *, full: bool, now: datetime | None = None) -> dict`.
    `labels` comes from `visibility_map`; a thread whose front-page `rel` is absent from it is
    skipped when not `full`. Shape:
    ```json
    {"schema_version": 1,
     "threads": [{"slug": "", "path": "", "title": "", "question": "", "status": "", "visibility": "",
                  "counts": {"open": 0, "answered": 0, "dropped": 0, "raised_7d": 0},
                  "unanswered_comments": 0, "scout_replies": [{"target": "", "stamp": ""}],
                  "experiments": {"planned": [], "running": [], "due": []},
                  "last_run": null, "problems": 0}],
     "needs_you": {"proposals": 0, "close_proposals": 0}}
    ```
    Threads are ordered by status (proposed, active, paused, closed, rejected), then title.
    `due` lists planned EIDs whose `not_before` ≤ now. `raised_7d` counts questions whose
    `raised` date is within 7 days of now. `close_proposals` counts threads where the last
    thread-level scout comment starts `Proposing to close`.
  - `lab_thread(vault: Path, slug: str, *, now=None) -> dict`: everything in `Thread`, as JSON:
    - questions with comments and replies
    - experiments, each with its note's frontmatter
    - evidence
    - the 50 newest timeline entries
    - problems as `{"line", "message"}`
    - `schema_version`
  - `session_context_kb.lab_block(vault, labels, *, full) -> dict` returns `lab_list(...)`, and
    `_trim_lab` drops closed and rejected threads first, then the oldest `last_run`.

- [ ] **Step 1: Write failing tests:**
  - `test_list_counts_fixture`: `counts == {"open": 2, "answered": 1, "dropped": 1, ...}`,
    `unanswered_comments == 1`, `experiments.planned == ["E2"]`, and `due == []` at a `now`
    before E2's `not_before`, `["E2"]` after it
  - `test_private_thread_hidden_from_delegate`: the thread has `visibility: private`, `labels`
    omits it, and with `full=False` the result has no threads
  - `test_thread_problems_surface`: a renamed heading gives `problems == 1` in the list, and
    `lab_thread(...)["problems"][0]["message"]` names the heading
  - `test_session_context_has_lab_block`: run the pre-session builder the way
    `test_session_context_kb.py` already does, and assert `"lab"` is in the output with
    `schema_version == 1`
  - In `test_lab_contract.py`: `test_lab_list_contract` and `test_lab_thread_contract` compare
    against the fixtures, or rewrite them when `SCOUT_REGEN_CONTRACT=1`. Follow
    `test_changes_contract.py`'s pattern, with a fixed `NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_lab_summary.py -q`. Expected:
  FAIL.
- [ ] **Step 3: Implement**, then generate the fixtures with
  `env SCOUT_REGEN_CONTRACT=1 .venv/bin/python -m pytest tests/unit/test_lab_contract.py`.
- [ ] **Step 4: Run**
  `.venv/bin/python -m pytest tests/unit/test_lab_summary.py tests/unit/test_lab_contract.py tests/unit/test_session_context_kb.py -q`.
  Expected: all pass.
- [ ] **Step 5: Commit** `feat(lab): list/show JSON contract and the session-context lab block`

### Task 7: `scoutctl lab` CLI

**Files:**
- Create: `scout/lab/cli.py`
- Modify: `scout/cli.py` (register it like `_register_action_items`, as `lab`)
- Test: `tests/unit/test_lab_cli.py`

**Interfaces:**
- Consumes: Tasks 3–6. The vault is resolved as the other commands resolve it (`paths.data_dir()`,
  overridable with `--vault`).
- Produces these commands. Each prints `ok <slug> <what>` on success, or JSON with `--json`
  where shown.
  - `list [--json]`, `show <slug> [--json]`
  - `approve|reject|pause|resume|close|reopen <slug> --author A [--reason R]`
  - `comment <slug> [--on Q3|E2] --author A --text T`
  - `reply <slug> [--on …] --to N --text T`
  - `veto <slug> <EID> --author A`
  - `propose --slug S --title T --question Q --open TEXT… [--evidence TARGET=WHY…] [--visibility private]`
  - `question add <slug> --text T [--by A]` (prints the QID),
    `question answer <slug> <QID> --note N`,
    `question drop <slug> <QID> --reason R [--owner-comment N]`
  - `understanding set <slug> --file F`
  - `evidence add <slug> --target T --why W`
  - `timeline add <slug> --tag TAG --did D --found F --questions Q [--steers S] [--evidence E]`
  - `experiment plan <slug> --short S --title T --question Q… --file F [--cap USD]`,
    `experiment start|abandon <slug> <EID> [--reason R]`,
    `experiment finish <slug> <EID> --result-file F --means-file F --one-line L`
  - `experiment run` and `experiment call` are added in Tasks 9 and 11
  - `lint [PATH…]` is added in Task 8
- `--author` on owner verbs and comments is folded through the same handle rule as action-item
  comments (`[A-Za-z][A-Za-z0-9._-]*`). An empty or invalid handle → `LabError`.

- [ ] **Step 1: Write failing tests** with `typer.testing.CliRunner` on a `tmp_path` vault
  holding the fixture thread:
  - `test_show_json_matches_lab_thread`
  - `test_comment_then_show_counts_unanswered`
  - `test_approve_requires_author`
  - `test_question_add_prints_qid`
  - `test_lab_error_exit_code_2`: `resume` on an active thread exits 2, and stderr contains
    `cannot resume an active thread`
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_lab_cli.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** thin wrappers over `ops`, `experiments` and `summary`.
- [ ] **Step 4: Run** the same command. Expected: all pass.
- [ ] **Step 5: Commit** `feat(lab): scoutctl lab commands`

### Task 8: `lab-format` lint check

**Files:**
- Create: `scout/lab/lint.py`
- Modify: `scout/kb/lint.py` (add `_lab` to `STAGED_CHECKS`), `scout/kb/lint_config.py` (add
  `"lab-format"` to `MODE_CHECKS`, `"lab-understanding-size"` to `WARNING_CHECKS`),
  `scout/lab/cli.py` (`lint` command)
- Test: `tests/unit/test_lab_lint.py`

**Interfaces:**
- Produces `lab_findings(repo: Path, rel: str, text: str) -> list[Finding]` for any `rel` under
  `knowledge-base/lab/`. It dispatches on the file kind:
  - front page:
    - every parser problem
    - duplicate QIDs or EIDs
    - `next_q` / `next_e` not above every ID used
    - a thread status outside the vocabulary
  - timeline:
    - a heading not `### YYYY-MM-DD HH:MM · <tag>` with a known tag
    - a QID or EID mentioned that the thread lacks
  - experiment note:
    - missing frontmatter keys (`id thread questions status planned not_before cost_cap_usd`)
    - `not_before < planned`
    - an unknown status
  - `lab.md`: a thread folder on disk that `lab.md` doesn't link

  These are blocking findings with check id `lab-format` and the line number when known.
  `Current understanding` over 2,048 bytes gives a non-blocking `lab-understanding-size`.
- `_lab(f: StagedFile) -> list[Finding]` reads the staged blob as the other checks do and calls
  `lab_findings`.

- [ ] **Step 1: Write failing tests:**
  - `test_clean_fixture_has_no_findings`
  - `test_duplicate_qid_blocks`
  - `test_timeline_unknown_qid_blocks`
  - `test_experiment_not_before_before_planned_blocks`
  - `test_index_missing_thread_blocks`
  - `test_every_finding_uses_a_known_check_id` (extend that existing test if it enumerates
    checks; otherwise assert the two ids are in `KNOWN_CHECKS`)
  - `test_staged_lab_file_blocks_commit`: through `lint_staged` on a temp git repo, following
    the existing staged-lint test fixture
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_lab_lint.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run**
  `.venv/bin/python -m pytest tests/unit/test_lab_lint.py tests/unit -k "lint" -q`. Expected: all
  pass.
- [ ] **Step 5: Commit** `feat(lab): lab-format lint check`

### Task 9: Model-call meter

**Files:**
- Create: `scout/lab/meter.py`
- Modify: `scout/lab/cli.py` (`experiment call`)
- Test: `tests/unit/test_lab_meter.py`
- Test helper: `tests/fixtures/lab/fake-claude.sh`. It prints
  `{"result":"echo:<stdin>","total_cost_usd":0.4}` and exits 0, so tests never need a real
  `claude`.

**Interfaces:**
- Produces:
  - `ENV_METER = "SCOUT_LAB_METER"`
  - `class MeterServer(sock_path: Path, ledger: Path, cap_usd: float, claude_argv: Sequence[str] = ("claude",), call_timeout_s: float = 600)`,
    with `start() -> None` (a daemon thread) and `stop() -> None`.
    - Protocol: one JSON line per connection, `{"v":1,"prompt":str,"model":str|None}`.
    - It answers with `{"ok":true,"text":…,"cost_usd":…,"spent_usd":…}`, or
      `{"ok":false,"refused":"cap","spent_usd":…,"cap_usd":…}`, or
      `{"ok":false,"error":…}`.
    - It refuses when the ledger's sum is ≥ the cap.
    - It runs `[*claude_argv, "-p", "--output-format", "json", *(["--model", m] if m else [])]`
      with the prompt on stdin and a clean environment. That environment is `PATH`, `HOME`,
      `LANG`, plus the Claude auth variables the scheduled runner already passes. Copy that list
      from `scripts/claude-with-retry.sh`, and don't invent one.
    - It reads `result` and `total_cost_usd` from the JSON.
    - It appends `{"at","cost_usd","ok"}` to the ledger, a JSONL file.
  - `meter_call(prompt: str, model: str | None = None, env: Mapping[str, str] | None = None) -> dict`
    (the client, connecting to `env[ENV_METER]`)
  - CLI `scoutctl lab experiment call [--model M] --prompt-file F`. It prints `text` and exits:
    - 0 on success
    - 3 when refused at the cap
    - 4 when the meter is unreachable or errors

- [ ] **Step 1: Write failing tests:**
  - `test_call_returns_text_and_records_cost`
  - `test_third_call_refused_at_cap_one_dollar`: at $0.40 a call, calls one and two succeed,
    the third is refused, and the ledger has 2 rows
  - `test_spent_survives_restart`: a new server on the same ledger refuses immediately
  - `test_cli_exit_codes`
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_lab_meter.py -q`. Expected:
  FAIL.
- [ ] **Step 3: Implement.** The unix-socket path must stay under 100 bytes, so tests use
  `tempfile.mkdtemp(dir="/tmp")`.
- [ ] **Step 4: Run** the same command. Expected: all pass.
- [ ] **Step 5: Commit** `feat(lab): metered model calls for experiments`

### Task 10: Experiment sandbox profile and runner

**Files:**
- Create: `scout/lab/sandbox.py`, `scout/lab/runner.py`
- Test: `tests/unit/test_lab_sandbox.py`, `tests/unit/test_lab_runner.py`

**Interfaces:**
- Consumes: `scout.sandbox.profile` (`Rule`, `render_rules`, `_quote`, `secrets_dirs`,
  `available`, `SANDBOX_EXEC`); Task 5 (`experiments`); Task 9 (`MeterServer`);
  `scout.kb.read.visibility_map`.
- Produces, in `sandbox.py`:
  `experiment_profile(exp_dir: Path, tmp_dir: Path, meter_sock: Path, deny_reads: Sequence[Path], deny_execs: Sequence[Path]) -> str`.
  The SBPL, in this order:
  - `(version 1)`, `(allow default)`
  - `(deny file-write* (subpath "/"))`
  - `(allow file-write* (subpath exp_dir) (subpath tmp_dir) (literal "/dev/null") (regex #"^/dev/tty"))`
  - `(deny file-read-data …)` for each `deny_reads` path plus every `secrets_dirs` subpath
  - `(deny network*)`, then
    `(allow network-outbound (remote unix-socket (path-literal "<meter_sock>")))`
  - `(deny process-exec …)` for each `deny_execs` literal

  Every path is resolved with `os.path.realpath` before rendering.
- Produces, in `runner.py`:
  - `@dataclass RunOutcome(exit: int, stdout_tail: str, stderr_tail: str, spent_usd: float, timed_out: bool)`
  - `run_experiment(vault: Path, slug: str, eid: str, argv: Sequence[str], *, cap_usd: float, timeout_s: float = 1800, env: Mapping[str, str] | None = None, claude_argv: Sequence[str] = ("claude",)) -> RunOutcome`.
    - It raises `LabError` unless `profile.available()` and the experiment status is `running`.
    - `exp_dir = paths.experiment_dir(note)` (created), `tmp_dir = exp_dir/".tmp"` (created,
      then removed afterwards).
    - `deny_reads` is every `knowledge-base/**` file that `visibility_map` labels `private`,
      unless the experiment's `visibility` is `private`. Call it with a reader from
      `scout.kb.privacy.FULL_READERS`, the way `pre_session_data` does.
    - `deny_execs` is the realpath of `git` and `claude` found on `PATH` (missing ones skipped).
    - It starts a `MeterServer` with ledger `exp_dir/"model-calls.jsonl"` on a socket in a fresh
      0700 `mkdtemp(dir="/tmp")`.
    - It runs `[SANDBOX_EXEC, "-p", profile, *argv]` with `cwd=exp_dir`, `start_new_session=True`,
      and env `{PATH, HOME, LANG, TMPDIR=tmp_dir, PYTHONDONTWRITEBYTECODE=1, SCOUT_LAB_METER=sock, SCOUT_LAB_EXPERIMENT_DIR=exp_dir}`.
    - At the timeout it sends `SIGKILL` to the process group and sets `timed_out=True`.
    - It writes `exp_dir/run-<YYYYMMDD-HHMM>.log` with argv, exit, the tails and spent.
    - It stops the meter in `finally`.

- [ ] **Step 1: Write failing tests**, marked
  `pytest.mark.skipif(not profile.available(), reason="needs sandbox-exec")` except the first:
  - `test_profile_text_orders_deny_before_allow`. A pure string test: the
    `deny file-write* (subpath "/")` position comes before the `allow file-write*` position, and
    the meter socket path appears in a `path-literal`.
  - `test_write_outside_experiment_dir_refused`. A script `echo x > <vault>/knowledge-base/x.md`
    exits non-zero and the file doesn't exist. `echo y > out.txt` in cwd succeeds.
  - `test_direct_network_refused`. A script `/usr/bin/curl -sS -m 5 https://example.com` exits
    non-zero. Separately, a python one-liner calling `meter_call` through the CLI succeeds
    against `fake-claude.sh`.
  - `test_private_file_unreadable`. With a `private`-labeled KB file, `cat` of it fails for a
    normal experiment and succeeds when the experiment is `visibility: private`.
  - `test_over_cap_call_refused_inside_sandbox`. With cap 0.5 and a fake claude at 0.4, the
    second `experiment call` exits 3.
  - `test_timeout_kills_process_group`. The script runs `sleep 300 & sleep 300`; with
    `timeout_s=2`, `timed_out is True`, and no `sleep 300` child with the recorded pgid is left
    (check with `os.killpg(pgid, 0)` raising `ProcessLookupError`).
  - `test_refuses_unless_running`
- [ ] **Step 2: Run**
  `.venv/bin/python -m pytest tests/unit/test_lab_sandbox.py tests/unit/test_lab_runner.py -q`.
  Expected: FAIL.
- [ ] **Step 3: Implement.** If the kernel rejects a form, fix the profile rather than loosening
  the test. Each refusal test is the spec's §12 step-1 requirement.
- [ ] **Step 4: Run** the same command on macOS. Expected: all pass, none skipped.
- [ ] **Step 5: Commit** `feat(lab): sandboxed, metered experiment runner`

### Task 11: Broker `experiment` verb and `scoutctl lab experiment run`

**Files:**
- Modify: `scout/sandbox/broker.py`
- Modify: `scout/lab/cli.py` (`experiment run`)
- Test: `tests/unit/test_run_broker_experiment.py` (follow `test_run_broker_inprocess.py`'s
  in-process `Broker` pattern)

**Interfaces:**
- Consumes: Task 10 `run_experiment`; Task 5 experiment notes.
- Produces in `broker.py`:
  - `VERBS = ("commit", "telegram", "experiment")`
  - `@dataclass(frozen=True) LabLimits(experiments_per_run: int = 1, max_cost_cap_usd: float = 1.0, notice_hours: float = 6, timeout_s: float = 1800)`,
    with `load_lab_limits(env) -> LabLimits` reading `config.json`'s `"lab"` block. Same rules
    as `load_limits`: a bad value means the default. It is held on `Broker` as `lab_limits`,
    filled in `for_vault`.
  - `validate_experiment(req: Mapping[str, Any], vault: Path, limits: LabLimits, now: datetime) -> tuple[str, str, list[str], float]`
    returning `(slug, eid, argv, cap)`. It raises `Refused(EXIT_REFUSED, reason)` when:
    - the slug or EID doesn't match its regex
    - `argv` is not 1–64 strings of ≤ 4,096 characters without NUL or control characters
    - there's no experiment note
    - the note's status at **HEAD** (`git show HEAD:<rel>`) is `vetoed`, or the note isn't at
      HEAD
    - the note's first commit (`git log --diff-filter=A --format=%ct -- <rel>`, the oldest) is
      newer than `now - notice_hours`

    The cap is `min(note.cost_cap_usd, limits.max_cost_cap_usd)`.
  - `Broker.experiments_run: int = 0` and `Broker.experiment(req) -> Reply`. Past
    `experiments_per_run` it returns
    `Reply(EXIT_LIMIT, "scoutctl lab: this run has run its <n> experiment(s)\n", kind=RUN_LIMIT)`.
    Otherwise it validates, calls `run_experiment(..., cap_usd=cap, timeout_s=limits.timeout_s)`,
    increments the count, and returns `Reply(outcome.exit, outcome.stderr_tail, outcome.stdout_tail)`.
    An experiment request is **never** queued for drain.
  - `client_experiment(slug: str, eid: str, argv: Sequence[str], env: Mapping[str, str] | None = None) -> int`:
    - With `ENV_SOCKET` set, it calls the broker with `timeout = limits.timeout_s + 60`, prints
      the reply as `_emit` does, and returns its exit.
    - On `Unreachable`, it prints "experiment not run: this run's broker didn't answer" and
      returns 4.
    - With no `ENV_SOCKET` and `SCOUT_MODE` set (a scheduled run without brokers), it prints
      "experiment not run: experiments need the run broker" and returns 4.
    - With neither (an interactive owner session), it calls `run_experiment` directly with the
      owner limits.
- CLI: `scoutctl lab experiment run <slug> <EID> -- CMD…` calls `client_experiment`.

- [ ] **Step 1: Write failing tests:**
  - `test_refuses_note_committed_too_recently`: commit the note at time T, and with now = T + 1 h
    the broker refuses with `notice`
  - `test_refuses_vetoed_at_head_even_if_worktree_says_planned`: commit `vetoed`, then rewrite
    the worktree to `planned` without committing; the broker refuses
  - `test_cap_clamped_to_owner_max`: the note says 5.00 and the owner max is 1.00, so
    `run_experiment` gets `cap_usd=1.0` (monkeypatched)
  - `test_second_experiment_in_one_run_hits_limit`
  - `test_unknown_verb_still_refused` (a regression check on `handle`)
  - `test_client_without_broker_in_scheduled_mode_returns_4`
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_run_broker_experiment.py -q`.
  Expected: FAIL.
- [ ] **Step 3: Implement.** Add the verb to `Broker.handle`.
- [ ] **Step 4: Run**
  `.venv/bin/python -m pytest tests/unit/test_run_broker_experiment.py tests/unit/test_run_broker.py tests/unit/test_run_broker_inprocess.py -q`.
  Expected: all pass.
- [ ] **Step 5: Commit** `feat(lab): broker experiment verb with notice, veto and owner limits`

### Task 12: Changelog, full suite, PR

**Files:**
- Create: `plugin/changelog.d/research-lab-design.added.md`. Two bullets: the `scoutctl lab`
  commands with the `lab-format` check, and sandboxed metered experiments run by the broker.
  Neither bullet mentions any vault's content.

- [ ] **Step 1: Run the full engine suite:**
  `.venv/bin/python -m pytest -n auto -q && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy scout/lab`.
  Expected: everything passes, and the sandbox tests ran (not skipped) on macOS.
- [ ] **Step 2: Run the changelog check:** `.venv/bin/python -m scout.scripts.changelog check`.
  Expected: exit 0.
- [ ] **Step 3: Commit** `docs(lab): changelog fragment`
- [ ] **Step 4: Push the branch and open a draft PR** against `main` on `Raven-Scout/Scout`
  titled `feat(lab): the Lab, Plan 1 — format, scoutctl lab, lint, experiment sandbox`. The body
  links the spec and this plan, lists the Review Focus items and how each is tested, and ends
  with the Claude Code attribution line. No vault content goes in the body.
