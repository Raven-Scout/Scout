# The Lab, Plan 2: the research brain

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make research runs work the Lab. A run answers owner comments first, then runs due
experiments, then advances one or two approved threads, then proposes threads, and its wrap
reports what moved. Before any run is told to run an experiment, the two sandbox gaps Plan 1
parked are closed.

**Architecture:**
- **Engine.** Three small engine changes come first, because the instructions depend on them:
  - private experiments fail closed, and a script can't read the working-tree copies of its
    own committed files;
  - `experiment run` ends with an unambiguous status line and takes a lowering `--timeout`;
  - the lab summary lists held experiments, and Scout gets a `propose-close` verb.
- **ML experiments (spec §6.4, decided 2026-11-23).** Five engine tasks let experiments train and
  probe small models: owner limits and note fields, a per-plan disk budget with a free-disk floor
  and a memory limit, detached runs that outlive the run, an owner-side weights cache, and the
  GPU. Each is off until the owner's config turns it on.
- **Heartbeat.** It fires research on lab state instead of the research queue.
- **Research phases.** The text under `plugin/phases/research/` is rewritten around threads,
  with one new phase file for experiments. Content tests pin the rules and check that every
  `scoutctl lab` command the text names exists. An `_assemble` test checks the text lands in
  RESEARCH.md only.

**Tech Stack:** Python 3.12, typer, pytest, macOS `sandbox-exec`, the Plan 1 `scout.lab` package,
`scout.scripts.heartbeat`, `scout.scripts.bootstrap._assemble`, `scout.scripts.phase_assembly`.

**Spec:** `docs/superpowers/specs/2026-11-21-research-lab-design.md`. Read §6, §7, §11 and
§14a. Plan 1 (`docs/superpowers/plans/2026-11-21-lab-plan-1-format-and-tools.md`) built
everything this plan calls. Its ledger's rulings R22–R34 explain the broker's checks.

## Global Constraints

- **Branch.** Create the worktree `~/.scout-worktrees/lab-plan-2` on a new branch
  `feat/lab-plan-2`, cut from the tip of `docs/research-lab-design` (Plan 1, draft PR
  Raven-Scout/Scout#761). Then run
  `python3.12 -m venv plugin/engine/.venv && plugin/engine/.venv/bin/pip install -e 'plugin/engine[dev]'`.
  Engine commands run from `plugin/engine` with `PYTHONPATH` set to the absolute engine path.
  A relative `.` breaks the tick-child and self-update tests.
- **PR.** Open a draft PR against `docs/research-lab-design`, so it stacks on #761. It stays a
  draft until Plans 3 and 4 are ready, and the three merge together (ruling P2-c).
- **Never run** `release-app.sh`, `release-plugin.sh` or any release script.
- **Fixtures and phase text are anonymized** (`plugin/CLAUDE.md`). Use people `Alex`, `Priya`
  and `Sam`, and comment author `alex`. Include no vault content, real names, companies or
  channels.
- **Phase text style:**
  - Paths are vault-relative.
  - Commands are `scripts/scoutctl …`, commits are `{{SCOUT_DIR}}/scripts/git-safe-commit.sh`
    with explicit paths (never `git add -A`), and times come from `scripts/scout-tz.sh`.
  - KB files are read through `scripts/scoutctl kb read`, never the Read tool. The existing
    `test_phase_privacy_rule.py` and `test_phase_explicit_path_commits.py` must keep passing.
- **Spec defaults** (already in `scout/defaults/scout-config.yaml` under `lab:`):
  - `target_threads` 5;
  - `max_proposals_per_run` 2;
  - `max_open_proposals` 5;
  - `stale_runs` 6;
  - `experiment_notice_hours` 6;
  - `experiment_cost_cap_usd` 1.00.
  Phase text names the config key next to the number, e.g. "at most 2 (`lab.max_proposals_per_run`)".
- **Status line vocabulary** (Task 3). Every `scoutctl lab experiment run` ends with exactly
  one stderr line that is one of:
  - `experiment ran: exit <n>`
  - `experiment refused: <reason>`
  - `experiment not run: <reason>`
  - `experiment started: detached` (Task 7, `--detach` only)

  `scoutctl lab experiment collect` ends with `experiment ran: exit <n>`,
  `experiment running: <elapsed> of <deadline>` or `experiment not run: <reason>` (Task 7).
- **Scheduled-run experiment timeout.** An attached run uses `--timeout 540`, with the Bash tool
  timeout at `600000` ms (ruling P2-b). Anything longer, and anything that names models, runs
  detached (ruling P2-h).
- **Owner limits for ML experiments** (spec §6.4), in the run broker's `config.json` `lab` block.
  The engine defaults: `detached_max_hours` 0, `max_detached` 0, `gpu` false,
  `max_memory_bytes` 0 (half the Mac's memory), `disk_free_floor_bytes` 15 000 000 000,
  `weights_max_bytes` 0, `python` unset, `max_total_bytes` 512 MiB. An example owner config sets
  12, 1, true, 0, 15 GB, 40 GiB, its own venv and 10 GiB; that edit is the owner's, outside this plan.
- **Kernel tests.** Every new kernel test skips under `SCOUT_TEST_NO_KERNEL=1`. The MPS tests also
  skip unless `SCOUT_TEST_TORCH_PYTHON` names an interpreter with torch. Never run them against
  `~/Scout`; fixture vaults only.
- **Gates.** `ruff check`, `ruff format --check` and `mypy scout/lab scout/sandbox` pass at every
  commit. Commit messages are `feat(lab): …`, `fix(lab): …`, `feat(research): …` or
  `test(lab): …`.

## Rulings made while planning (flag for review)

| Ruling | Decision | Cost if wrong |
|---|---|---|
| **P2-a** | Experiments in private threads are refused, instead of building per-thread label inheritance into the privacy layer now. The layer labels files one at a time in many places: git-blob mirrors, sync, lint. A cross-file rule there is a separate project. | Experiments over private data wait for a later plan. |
| **P2-b** | Scheduled runs cap each *attached* experiment at 540 s, because the Bash tool can't wait more than 10 minutes. Longer ones run detached (P2-h). | — |
| **P2-h** | Detached runs reuse the attached runner whole, through two hooks (`on_started`, `should_stop`), rather than a second runner. An experiment that names models must run detached, because the fetch happens at start. | A long fetch can't run attached even when the experiment itself is short. |
| **P2-i** | The ML work lands in Plan 2, before the phase text, because the phase text must name `--detach`, `collect` and the note fields. It stays off until the owner's config turns it on. | Plan 2's PR grows by five engine tasks. |
| **P2-j** | The MPS rules are found by experiment (Task 9, Step 3) and recorded rule by rule in spec §14b. If MPS needs a write outside the scratch dir that can't be redirected, the task stops for a ruling. | The GPU may need a wider sandbox than the spec allows. |
| **P2-c** | Plans 2–4 merge together. Plan 2 alone would leave runs that only propose threads, with no app button to approve them. | Nothing ships until all three are ready. |
| **P2-d** | The heartbeat drops the research-queue gate now. It fires on lab state instead, so a vault with fewer than 5 open proposals gets a research run every `research_min_gap_hours`. This is what spec §7 asks for. | More research runs on vaults whose queue used to be empty. |
| **P2-e** | An experiment marked `running` and left unrun because a run had no broker is retried by the next run. It is not abandoned. | One stuck experiment retried each run until it runs or the owner vetoes it. |
| **P2-f** | A new `scoutctl lab propose-close` verb. §6.1 needs a thread-level Scout comment, and Plan 1 has no command that writes one. | One more verb. |
| **P2-g** | When the broker refuses an experiment's request (notice, veto, uncommitted, argv), the run abandons the experiment with the refusal as the reason. If the work is still wanted, it plans a new EID. | A refusal caused by a transient git hiccup costs a re-plan and another 6 h wait. |

## Review Focus

1. **A script that itself exits 2, 4 or 5** must not read as a refusal or as "not run". The
   final status line tells them apart. Test in Task 3.
2. **An owner comment the run can't or won't act on** (out of scope, ambiguous, or asking for
   something forbidden). It still gets a reply that declines and gives the reason, in the run
   that reads it, and is never skipped. The phase text pins this. Test in Task 10.
3. **A vault with no `knowledge-base/lab/` at all.** The heartbeat says research is wanted,
   because a proposal is possible. `lab list --json` returns empty threads, not an error, and
   the phase text tells the run to propose from entity scoring. Tests in Tasks 4 and 10.
4. **The owner pauses a thread, or hand-breaks its front page, during a run.** The run's next
   write to it is refused. The run stops working that thread, still replies to its comments,
   reports the thread in the wrap, and never hand-edits around the refusal. Test in Task 10.
5. **A private thread with an unanswered owner comment:**
   - the heartbeat counts it;
   - the run replies;
   - the Slack wrap never names it, though the Telegram wrap may;
   - it is never a web or connector query target.
   Tests in Tasks 4 and 11.
6. **A Mac that restarts, sleeps or runs out of disk mid-experiment.** A restart leaves a record
   with no outcome; the next collect sweeps and reports `not run`, never `running` forever.
   Sleep spends the deadline. Free space falling under the floor kills the run, whatever filled
   the disk. Tests in Tasks 6 and 7.
7. **A model repo that carries code.** Pickled weights, `.py` files and `trust_remote_code`
   configs are refused, and nothing from them is left in the cache. Test in Task 8.

---

### Task 1: Private experiments fail closed (closes R33)

**Files:**
- Modify: `plugin/engine/scout/lab/experiments.py` (`plan`)
- Modify: `plugin/engine/scout/sandbox/broker.py` (`validate_experiment`)
- Modify: `plugin/engine/scout/lab/lint.py`
- Test: `tests/unit/test_lab_experiments.py`, `tests/unit/test_run_broker_experiment.py`,
  `tests/unit/test_lab_lint.py`

**Interfaces:**
- Consumes: `paths.front_page`, `paths.experiment_note`, the parsed `visibility` of a thread
  and a note, and the broker's existing `Refused` (exit 2) and history helpers.
- Produces:
  - `experiments.plan` raises `LabError` for a private thread;
  - `validate_experiment` raises `Refused` when the thread front page **or** the note is
    `visibility: private`, either at the validated commit or in the working tree;
  - lint reports any non-markdown file under a private thread's folder.

- [ ] **Step 1: Write the failing tests:**
  - `test_plan_refuses_in_a_private_thread`: plan in a fixture thread with
    `visibility: private`. `LabError` is raised, its message contains
    `experiments in a private thread aren't supported yet`, and no file is created under
    `experiments/`.
  - `test_broker_refuses_a_private_note` and `test_broker_refuses_a_private_thread`: parametrize
    over HEAD-only and worktree-only private, four cases in all. Each raises `Refused`, exits 2,
    and the reason contains `private`. The runner is never called (patch it to fail).
  - `test_lint_flags_non_markdown_in_a_private_thread`: a staged `run.py` under a private
    thread's `experiments/<slug>-E1-x/`. The finding names the path and contains
    `a private thread can't hold non-markdown files`.
- [ ] **Step 2: Run them; they fail.**
  `.venv/bin/python -m pytest tests/unit/test_lab_experiments.py tests/unit/test_run_broker_experiment.py tests/unit/test_lab_lint.py -q -k private`
- [ ] **Step 3: Implement.**
  - The broker check runs before the notice and veto checks, and uses the same commit
    `validate_experiment` already resolves.
  - Keep the runner's private-experiment code. Nothing reaches it now, and a later plan
    re-enables it with label inheritance.
- [ ] **Step 4: Run the Step 2 command, then the full lab and broker tests**
  (`-k 'lab or broker'`). All pass.
- [ ] **Step 5: Commit** `fix(lab): experiments in private threads are refused until their files inherit privacy`

### Task 2: Sandbox reads, part 2: committed siblings and home-installed interpreters (closes R34)

**Files:**
- Modify: `plugin/engine/scout/lab/runner.py`, `plugin/engine/scout/lab/sandbox.py`
- Test: `tests/unit/test_lab_runner.py`, `tests/unit/test_lab_sandbox.py`

**Interfaces:**
- Consumes: `run_experiment(..., commit=…)`, `_snapshot`, `experiment_profile`,
  `_interpreter_root`, `_allow_reads`, `SCOUT_LAB_SOURCE_DIR`.
- Produces:
  - **Sibling deny.** For every path that `git ls-tree -r -z --name-only <commit> -- <exp_rel>/`
    lists, the profile denies `file-read*` and `file-write*` on that path's working-tree copy
    (SBPL `literal`).
  - **Conda prefixes.** `_interpreter_root` returns a prefix directly inside a home folder when
    that prefix contains `conda-meta/` (e.g. `~/miniconda3`).
  - **Shims.** The runner raises
    `LabError("<path> is a version-manager shim; put the real interpreter first on PATH")` for
    an allowlisted interpreter whose realpath is under a `shims/` folder or is named
    `volta-shim`.

- [ ] **Step 1: Write the failing tests.**
  - **Kernel tests** (skip unless `profile.available()`):
    - `test_cwd_relative_sibling_cannot_read_the_worktree_copy`: commit `run.sh`
      (`sh ./helper.sh`) and `helper.sh` (`echo COMMITTED`), then edit the working-tree
      `helper.sh` to `echo UNCOMMITTED` without committing. Run `sh run.sh`. Assert
      `"UNCOMMITTED" not in stdout_tail` and that the exit is non-zero.
    - `test_source_dir_sibling_runs_the_committed_copy`: the same setup, with `run.sh` calling
      `sh "$SCOUT_LAB_SOURCE_DIR/helper.sh"`. Stdout contains `COMMITTED`, and the exit is 0.
    - `test_new_output_files_stay_readable_and_writable`: the script writes `out.csv` in its cwd
      and `cat`s it back. The exit is 0.
  - **Pure tests:**
    - `test_interpreter_root_allows_a_conda_base_prefix`: in a tmp home with
      `miniconda3/conda-meta/` and `miniconda3/bin/python3.12`, the root is
      `<home>/miniconda3`.
    - `test_interpreter_root_still_narrows_dot_local`: `<home>/.local/bin/python3` (no
      `conda-meta`) keeps today's answer.
    - `test_shim_interpreter_is_refused`: a `python3` script under `<home>/.pyenv/shims/` first
      on PATH. `LabError` is raised with `version-manager shim` in the message.
- [ ] **Step 2: Run them; they fail.**
  `.venv/bin/python -m pytest tests/unit/test_lab_runner.py tests/unit/test_lab_sandbox.py -q -k 'sibling or source_dir or output_files or interpreter_root or shim'`
- [ ] **Step 3: Implement.**
  - Build the deny list from the same commit the snapshot uses.
  - A path SBPL can't carry fails the run through the existing `ProfileError` route.
  - Record in the runner's threat-model comment: a script that hardlinks a committed file before
    reading it escapes the sibling deny. That hardlink would have to be in the committed script,
    which the owner sees during the notice.
- [ ] **Step 4: Run the Step 2 command, then `-k 'lab'`.** All pass. The kernel tests ran and
  were not skipped.
- [ ] **Step 5: Commit** `fix(lab): scripts can't read the working-tree copies of their committed files; conda prefixes; refuse shims`

### Task 3: Run-facing verbs: status line, `--timeout`, held experiments, `propose-close`

**Files:**
- Modify: `plugin/engine/scout/lab/cli.py`, `plugin/engine/scout/sandbox/broker.py`
  (`client_experiment`, the `experiment` verb), `plugin/engine/scout/lab/summary.py`,
  `plugin/engine/scout/lab/ops.py`
- Modify: the lab-list contract fixture under `tests/fixtures/` that `test_lab_contract.py`
  compares against
- Test: `tests/unit/test_lab_cli.py`, `tests/unit/test_run_broker_experiment.py`,
  `tests/unit/test_lab_summary.py`, `tests/unit/test_lab_ops.py`, `tests/unit/test_lab_contract.py`

**Interfaces:**
- Consumes: `client_experiment(slug, eid, argv, env)`, `LabLimits.timeout_s`,
  `Thread.unanswered_owner_comments()`, `_CLOSE_PREFIX = "Proposing to close"`.
- Produces:
  - **`--timeout`.**
    - `scoutctl lab experiment run <slug> <EID> [--timeout SECONDS] -- CMD…`. `SECONDS` is an
      int ≥ 1; the effective timeout is `min(SECONDS, owner timeout_s)`.
    - The broker request gains an optional `"timeout_s"`, and the broker applies the same `min`.
    - The signature becomes `client_experiment(slug, eid, argv, env=None, *, timeout_s: int | None = None) -> int`.
  - **Status line.** The final stderr line is exactly one of the three in Global Constraints.
    Exit codes don't change.
  - **Held experiments.** `lab_list` rows gain `experiments.held: list[str]`: the `due` EIDs
    whose experiment line, or any question in the note's `questions`, has an unanswered owner
    comment. `due` itself doesn't change.
  - **`propose-close`.**
    - `ops.propose_close(vault: Path, slug: str, reason: str) -> None` appends a thread-level
      Scout comment `Proposing to close: <reason>`. It is allowed in `active` and `paused`
      threads and refused in any other status.
    - CLI: `scoutctl lab propose-close <slug> --reason TEXT`, which prints
      `ok <slug> propose-close`.

- [ ] **Step 1: Write the failing tests.**
  - `test_status_line_when_the_script_exits_2`: an owner-path run of a committed script that
    exits 2. The process exit is 2, and the last stderr line is `experiment ran: exit 2`.
    Repeat for exits 4 and 5.
  - `test_status_line_on_refusal`: a veto, say. The last line starts `experiment refused: `.
  - `test_status_line_without_broker_in_a_scheduled_run`: `SCOUT_MODE=research` and no socket.
    The last line starts `experiment not run: `, and the exit is 4.
  - `test_timeout_only_lowers`:
    - with `--timeout 5` against an owner limit of 1800, a `sleep 30` script is killed, the
      outcome is `timed_out`, and it ends within 30 s;
    - with `--timeout 99999`, the runner receives the owner limit (patch the runner and assert
      the `timeout_s` argument).
  - `test_broker_applies_requested_timeout_min`: a request `timeout_s` of 60 against an owner
    limit of 1800 calls the runner with 60.
  - `test_held_lists_due_experiments_with_unanswered_owner_comments`: the fixture's E2 is
    targeted by Q3, which carries alex's unanswered comment. Once E2 is due,
    `held == ["E2"]`. After a Scout reply to that comment, `held == []`.
  - `test_propose_close_counts_in_needs_you`: after `propose_close`,
    `lab_list(...)["needs_you"]["close_proposals"] == 1`, `unanswered_comments` doesn't change,
    and a `proposed` thread refuses.
  - The contract test passes once the fixture is regenerated with `held`.
- [ ] **Step 2: Run them; they fail.**
  `.venv/bin/python -m pytest tests/unit/test_lab_cli.py tests/unit/test_run_broker_experiment.py tests/unit/test_lab_summary.py tests/unit/test_lab_ops.py tests/unit/test_lab_contract.py -q`
- [ ] **Step 3: Implement.**
  - **Status line.** The client prints it after everything else. On the broker path the client
    needs to know whether the reply is a refusal or an outcome, so carry that in the reply in
    whatever form the protocol already allows.
  - **Refusals.** Replace the current `scoutctl lab: <reason>` refusal prints with the
    `experiment refused:` form.
  - **Timeline.** `propose-close` writes only the comment, not a timeline entry; the run's own
    `research run` entry covers it.
- [ ] **Step 4: Run the Step 2 command, then `-k 'lab or broker'`.** All pass.
- [ ] **Step 5: Commit** `feat(lab): experiment status line and --timeout, held experiments, propose-close`

### Task 4: Heartbeat fires research on lab state

**Files:**
- Modify: `plugin/engine/scout/scripts/heartbeat.py`
- Test: `tests/unit/test_heartbeat.py`, `tests/unit/test_heartbeat_run.py`,
  `tests/unit/test_privacy_runners.py`

**Interfaces:**
- Consumes:
  - `scout.lab.summary.lab_list(vault, None, full=True)`;
  - the vault's `lab:` config, read the same way `ops.propose` reads `max_open_proposals`.
- Produces:
  - `research_wanted(vault: Path) -> bool` is True when any of these holds:
    - a thread is `active` with `visibility` not private;
    - an `active` or `paused` thread of any visibility has `unanswered_comments > 0`;
    - open proposals (`needs_you.proposals`) number fewer than `lab.max_open_proposals`.
  - Spec §7's "an experiment is due" needs no condition of its own. Experiments run only in
    active threads; a normal active thread already fires; and Task 1 refuses experiments in
    private threads.
  - An exception while reading the lab prints one stderr line, `heartbeat: lab unreadable
    (<ExcType>); research allowed`, and returns True.
  - `decide(...)`'s `research_queue_open: bool` parameter becomes `research_wanted: bool`.
  - Remove `research_queue_has_open`, `research_queue_has_unchecked`, the `_RESEARCH_QUEUE_*`
    constants and their tests. Other research-queue references (`kb_pre_filter`,
    `migrate_perfile`, `bootstrap`, the session-context `research_queue` block) belong to
    Plan 4. Leave them.

- [ ] **Step 1: Write the failing tests** in `test_heartbeat.py`:
  - `test_research_wanted_without_a_lab_dir`: no `knowledge-base/lab/` → True.
  - `test_research_not_wanted_with_five_open_proposals_and_nothing_else`: five `proposed`
    threads, none active → False.
  - `test_research_wanted_for_an_unanswered_comment_in_a_private_paused_thread`: the five
    proposals plus that thread → True.
  - `test_private_active_thread_alone_does_not_want_research`: the five proposals plus one
    private `active` thread with no comments or experiments → False.
  - `test_lab_read_error_allows_research`: patch `lab_list` to raise → True, with the stderr
    line.
  - Update the existing `decide` tests for the renamed parameter, and delete the queue tests in
    `test_privacy_runners.py` lines ~258–280.
- [ ] **Step 2: Run them; they fail.**
  `.venv/bin/python -m pytest tests/unit/test_heartbeat.py tests/unit/test_heartbeat_run.py tests/unit/test_privacy_runners.py -q`
- [ ] **Step 3: Implement.** Import `scout.lab` lazily inside `research_wanted`; heartbeat cold
  start is measured. Update the module docstring's research bullet.
- [ ] **Step 4: Run the Step 2 command, then `-k heartbeat`.** All pass.
- [ ] **Step 5: Commit** `feat(lab): the heartbeat fires research on lab state, not the research queue`

### Task 5: ML experiments, part 1: owner limits and note fields (spec §6.4)

**Files:**
- Modify: `plugin/engine/scout/sandbox/broker.py` (`LabLimits`, `load_lab_limits`, `validate_experiment`)
- Modify: `plugin/engine/scout/lab/parser.py`, `plugin/engine/scout/lab/lint.py`, `plugin/engine/scout/lab/experiments.py` (`plan`)
- Modify: `plugin/engine/scout/lab/cli.py` (`experiment plan` flags)
- Test: `tests/unit/test_run_broker_experiment.py`, `tests/unit/test_lab_parser.py`, `tests/unit/test_lab_lint.py`, `tests/unit/test_lab_experiments.py`

**Interfaces:**
- Produces, on `LabLimits` (all read from the owner's `config.json` `lab` block like the existing keys; a
  missing or bad value means the default):
  - `detached_max_hours: float = 0` (0 = detached runs off; at most 72)
  - `max_detached: int = 0` (0 = off; at most 8)
  - `gpu: bool = False` (on only for JSON `true`)
  - `max_memory_bytes: int = 0` (0 = half of `hw.memsize`; at most 1 TiB)
  - `disk_free_floor_bytes: int = 15_000_000_000` (may be 0; at most 1 TiB)
  - `weights_max_bytes: int = 0` (0 = no fetching; at most 1 TiB)
  - `python: str | None = None` (an absolute path to an existing executable, else `None`)
- Produces, on the parsed experiment (`scout.lab.parser.parse_experiment`), from frontmatter:
  - `timeout_hours: float | None` (positive, finite)
  - `models: list[ModelRef]` with `ModelRef(repo: str, revision: str)` in `scout/lab/model.py`;
    `repo` matches `^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$`, `revision` matches `^[0-9a-f]{40}$`
  - `disk_budget_bytes: int | None` (positive)
- Produces: `validate_experiment` returns a sixth value, `ExperimentPlan(timeout_hours, models, disk_budget_bytes)`
  as read **at the validated commit**, with the budget clamped to `max_total_bytes` and `None` → 512 MiB.
- Produces: `lab experiment plan … [--timeout-hours H] [--model REPO@REVISION]… [--disk-budget BYTES]`.

- [ ] **Step 1: Write the failing tests:**
  - `test_lab_limits_read_the_ml_keys`: a config with every new key set reads back each value; one with
    `"gpu": 1`, `"max_detached": -1`, `"python": "relative/py"` and `"detached_max_hours": 1e400` reads the defaults.
  - `test_experiment_ml_fields_parse`: a note with `timeout_hours: 6`, two `models` and
    `disk_budget_bytes: 8589934592` parses to those values.
  - `test_lint_flags_bad_ml_fields`: `revision: main`, `repo: ../x`, `timeout_hours: 0` and
    `disk_budget_bytes: -1` each give one finding naming the field.
  - `test_broker_reads_the_plan_at_the_commit`: a worktree edit raising `disk_budget_bytes` after the commit
    doesn't change the returned plan (the uncommitted-change refusal already covers the run; this pins the source).
  - `test_disk_budget_is_clamped`: a 20 GiB budget under a 10 GiB owner maximum returns 10 GiB; no budget returns 512 MiB.
- [ ] **Step 2: Run them; they fail.**
  `.venv/bin/python -m pytest tests/unit/test_run_broker_experiment.py tests/unit/test_lab_parser.py tests/unit/test_lab_lint.py tests/unit/test_lab_experiments.py -q -k 'ml or budget or plan_at'`
- [ ] **Step 3: Implement.** Keep every existing caller of `validate_experiment` working; update the
  broker's `experiment` verb to unpack the sixth value (it is used from Task 6 on).
- [ ] **Step 4: Run the Step 2 command, then `-k 'lab or broker'`.** All pass.
- [ ] **Step 5: Commit** `feat(lab): owner limits and note fields for ML experiments`

### Task 6: ML experiments, part 2: the disk budget, the free-disk floor, memory and checkpoints

**Files:**
- Modify: `plugin/engine/scout/lab/runner.py` (`run_experiment`, the poll loop, `RunOutcome`)
- Modify: `plugin/engine/scout/sandbox/broker.py` (the `experiment` verb, `validate_commit`)
- Modify: `plugin/templates/.gitignore.tmpl`
- Modify: `plugin/engine/scout/lab/summary.py` (`checkpoint_bytes` per thread) and the lab-list contract fixture
- Test: `tests/unit/test_lab_runner.py`, `tests/unit/test_run_broker_experiment.py`,
  `tests/unit/test_run_broker.py`, `tests/unit/test_upgrade_merges_gitignore.py`, `tests/unit/test_lab_contract.py`

**Interfaces:**
- Consumes: Task 5's `ExperimentPlan.disk_budget_bytes`, `LabLimits.disk_free_floor_bytes`, `LabLimits.max_memory_bytes`.
- Produces:
  - `run_experiment(..., max_total_bytes=<the plan's budget>, disk_free_floor_bytes: int, max_memory_bytes: int)`;
    the broker passes the plan's budget, no longer the owner maximum.
  - `RunOutcome.over_disk_floor: bool` and `RunOutcome.over_memory: bool`, in the run log beside `over_bytes`.
  - Before launch, `free_bytes(vault) - budget < floor` raises `LabError` with
    `free disk would fall below the floor`; the broker replies `experiment not run: free disk would fall below the floor`
    (exit 4, the run's allowance kept).
  - `scout.lab.runner.footprint_bytes(pid) -> int | None` (physical footprint, GPU allocations included).
  - `validate_commit` refuses a file over `LAB_COMMIT_MAX_FILE_BYTES = 10 * 1024 * 1024` under `knowledge-base/lab/`
    with a reason naming the path and `checkpoints/`.
  - The template gains `knowledge-base/lab/*/experiments/*/checkpoints/`.
  - `lab list --json` gives each thread `checkpoint_bytes`: the bytes under its experiments' `checkpoints/`
    folders (`lstat`, no link followed), for the wrap.

- [ ] **Step 1: Write the failing tests:**
  - `test_run_refuses_to_start_below_the_floor`: patch `free_bytes` to `floor + budget - 1`. `LabError`, the
    script never ran (its marker file is absent).
  - `test_run_stops_when_free_space_falls_below_the_floor`: free space drops below the floor after the second
    poll. The outcome has `over_disk_floor=True` and the run log says so.
  - `test_run_stops_over_memory`: `footprint_bytes` reports more than `max_memory_bytes` for a holder. The outcome
    has `over_memory=True`.
  - `test_default_memory_limit_is_half_the_mac`: with `max_memory_bytes=0` the limit used is `hw.memsize // 2`.
  - `test_broker_runs_with_the_plans_budget`: the runner receives the clamped plan budget.
  - `test_commit_refuses_a_large_lab_file` (11 MiB under an experiment folder: refused, reason names the path) and
    `test_commit_allows_a_large_file_elsewhere` (the 10 MiB rule is the lab's only).
  - `test_gitignore_template_excludes_checkpoints`: the template, merged into a fixture vault, ignores
    `knowledge-base/lab/t/experiments/t-E1-x/checkpoints/a.safetensors`, checked with `git check-ignore`.
  - `test_contract_checkpoint_bytes`: the lab-list fixture carries `checkpoint_bytes`.
  - Kernel (skipped under `SCOUT_TEST_NO_KERNEL=1`): `test_kernel_budget_kills_a_writer`: a script writing past a
    1 MiB budget is killed with `over_bytes`.
- [ ] **Step 2: Run them; they fail.**
  `.venv/bin/python -m pytest tests/unit/test_lab_runner.py tests/unit/test_run_broker_experiment.py tests/unit/test_run_broker.py tests/unit/test_upgrade_merges_gitignore.py -q -k 'floor or memory or budget or large or checkpoints'`
- [ ] **Step 3: Implement.** The floor and memory checks run at the existing `_POLL_S` polls, and each kill goes
  through the existing kill path. `free_bytes` is `os.statvfs` on the resolved vault (`f_bavail * f_frsize`).
- [ ] **Step 4: Run the Step 2 command with the kernel on, then `-k 'lab or broker'`.** All pass.
- [ ] **Step 5: Commit** `feat(lab): a per-plan disk budget, a free-disk floor, a memory limit and uncommitted checkpoints`

### Task 7: ML experiments, part 3: detached runs

**Files:**
- Create: `plugin/engine/scout/lab/supervise.py` (the supervisor, `python -m scout.lab.supervise`)
- Create: `plugin/engine/scout/lab/runs.py` (the owner-side state record)
- Modify: `plugin/engine/scout/lab/runner.py` (hooks for the supervisor)
- Modify: `plugin/engine/scout/sandbox/broker.py` (`detach` on the `experiment` verb, a new `experiment-status` verb, `_emit_experiment`)
- Modify: `plugin/engine/scout/lab/cli.py` (`experiment run --detach`, `experiment collect`, `experiment stop`)
- Modify: `plugin/engine/scout/lab/summary.py` (`running` experiments carry `detached`, `elapsed`, `deadline`)
- Test: `tests/unit/test_lab_supervise.py` (new), `tests/unit/test_run_broker_experiment.py`, `tests/unit/test_lab_cli.py`, the lab-list contract fixture

**Interfaces:**
- Consumes: Task 5's `ExperimentPlan.timeout_hours`, `LabLimits.detached_max_hours` and `max_detached`;
  Task 6's runner arguments; `owner_veto`.
- Produces:
  - `runs.record_path(vault, slug, eid, env) -> Path`: `owner_dir(env) / "lab-runs" / f"{vault_hash}-{slug}-{eid}.json"`, folder 0700.
  - `runs.RunRecord(started: str, deadline: str, pid: int, probe: str | None, marker: str | None, outcome: dict[str, Any] | None)`
    (`probe` and `marker` are set by `on_started`, once the gate's checks pass),
    `runs.read(path) -> RunRecord | None`, `runs.write(path, record)` (atomic, 0600).
  - `runs.live(vault, env) -> list[RunRecord]`: records with no outcome whose `pid` is alive.
  - `run_experiment(..., on_started: Callable[[str, str], None] | None = None, should_stop: Callable[[], str | None] | None = None)`:
    `on_started(probe, marker)` is called once the gate's kernel checks pass, before "go"; `should_stop()` is polled
    and a returned reason kills the run through the timeout path, recorded as `RunOutcome.stopped: str | None`.
  - `supervise.start(vault, slug, eid, argv, *, commit, cap_usd, deadline_s, limits, env) -> None`: double-forks a
    supervisor in its own session, outside every sandbox, and returns once its record exists (started, deadline,
    pid). It doesn't wait for the gate: Task 8's fetch runs first. Raises `LabError` when the supervisor exits
    first or no record appears within 30 s.
  - Broker `experiment` with `"detach": true`: the same checks, then refuses `detached runs are off in the owner's
    config` when `detached_max_hours == 0` and `a detached experiment is already running` when `len(runs.live(...)) >= max_detached`;
    deadline = `min(plan.timeout_hours, detached_max_hours)` hours, or `detached_max_hours` when the plan sets none.
    The reply's status line is `experiment started: detached`. It counts toward `experiments_per_run`.
  - Broker `experiment-status` (`slug`, `eid`): a reply whose last line is `experiment ran: exit <n>`,
    `experiment running: <elapsed> of <deadline>` (both `HhMm`), or `experiment not run: <reason>`. A record with
    no outcome and a dead `pid` is swept first when it has a `probe` (the runner's `_sweep` with the record's
    `probe` and `marker`; without one, no script was started), then
    answered `experiment not run: the supervisor stopped without a result` and given that outcome.
  - `lab experiment run … --detach` sends `detach`; `lab experiment collect <slug> <EID>` sends `experiment-status`
    (no broker: read the record directly, the owner's session); `lab experiment stop <slug> <EID> --by <author>`
    (owner verb) SIGTERMs the supervisor, which records `stopped by the owner`.
  - The supervisor's `should_stop` returns `stopped by the owner` when `owner_veto(env, slug, eid)` finds a veto.
  - The run log gains `detached: true`, `deadline` and `stopped`.

- [ ] **Step 1: Write the failing tests:**
  - `test_detach_refused_when_off` and `test_detach_refused_when_one_is_running`: exact reasons above; the
    supervisor is never started (patch `supervise.start` to fail).
  - `test_detach_deadline_is_clamped`: plan 24 h under a 12 h owner maximum → `deadline_s == 43200`.
  - `test_detached_start_replies_started`: the stderr's last line is `experiment started: detached`, and the run's
    allowance is used.
  - `test_status_reports_running`, `test_status_reports_the_outcome`: from fixture records.
  - `test_detached_start_does_not_wait_for_the_gate`: a supervisor whose fetch blocks still lets `start` return
    within 30 s, and the record has no `probe` yet.
  - `test_failed_gate_is_the_outcome`: a supervisor whose gate check fails records `not run: <reason>`, and
    `experiment-status` replies with it.
  - `test_status_sweeps_a_dead_supervisor`: a record with no outcome and a dead pid. `_sweep` is called with its
    probe and marker, the reply is `experiment not run: the supervisor stopped without a result`, and the record
    now carries that outcome (a second status gives the same answer without sweeping again).
  - `test_record_is_out_of_the_runs_reach`: `profile.decide(profile.build_rules(vault, env), record_path(...))`
    is `"deny"` for writes.
  - `test_veto_stops_a_running_experiment`: `should_stop` returns `stopped by the owner` once a veto is recorded.
  - `test_collect_without_a_broker_reads_the_record` and `test_stop_signals_the_supervisor` (CLI, the pid patched).
  - `test_contract_running_detached`: the lab-list fixture's running experiment carries `detached`, `elapsed` and
    `deadline`.
  - Kernel: `test_kernel_detached_run_outlives_its_broker`: start a detached `sh -c 'sleep 3; echo done > out.txt'`,
    stop the broker, wait 5 s; `collect` replies `experiment ran: exit 0` and `out.txt` holds `done`.
  - Kernel: `test_kernel_killed_supervisor_leaves_nothing`: SIGKILL the supervisor of a `sleep 60` script; the next
    `collect` sweeps, and no process holds the run's sandbox afterwards.
- [ ] **Step 2: Run them; they fail.**
  `.venv/bin/python -m pytest tests/unit/test_lab_supervise.py tests/unit/test_run_broker_experiment.py tests/unit/test_lab_cli.py tests/unit/test_lab_contract.py -q`
- [ ] **Step 3: Implement.** The supervisor holds the experiment's existing run lock for its whole life, so an
  attached run of the same experiment is refused `already running`. It writes the outcome to the record before it
  releases the lock. The record never holds the meter token.
- [ ] **Step 4: Run the Step 2 command with the kernel on, then `-k 'lab or broker'`.** All pass.
- [ ] **Step 5: Commit** `feat(lab): detached experiments: a supervisor outlives the run, a later run collects it`

### Task 8: ML experiments, part 4: the weights cache and the owner's Python

**Files:**
- Create: `plugin/engine/scout/lab/weights.py`
- Modify: `plugin/engine/scout/lab/supervise.py` (fetch before the gate), `plugin/engine/scout/lab/runner.py`
  (`$SCOUT_LAB_WEIGHTS_DIR`, `lab.python`), `plugin/engine/scout/lab/sandbox.py` (the cache's read allow-back),
  `plugin/engine/scout/sandbox/broker.py` (an experiment with `models` must be detached)
- Test: `tests/unit/test_lab_weights.py` (new), `tests/unit/test_lab_sandbox.py`, `tests/unit/test_run_broker_experiment.py`

**Interfaces:**
- Consumes: Task 5's `ModelRef`, `LabLimits.weights_max_bytes`, `disk_free_floor_bytes`, `python`; Task 7's supervisor.
- Produces:
  - `weights.cache_root(env) -> Path`: `HOME/Library/Caches/Scout/lab-weights` (resolved under `env`'s HOME).
  - `weights.ALLOWED_SUFFIXES = (".safetensors", ".json", ".txt", ".model", ".tiktoken")`.
  - `weights.fetch(models, *, root, max_bytes, floor_bytes, in_use, token, http) -> list[Fetched]` with
    `Fetched(repo, revision, path, size, sha256)`. `http` is an injected client (tests never touch the network);
    the real one is plain HTTPS to `https://huggingface.co` (`/api/models/<repo>/revision/<rev>` for the file list,
    `/<repo>/resolve/<rev>/<file>` for each file). Files land in `root/<repo>/<revision>/` via a temp name and rename.
    Raises `LabError` with one of: `no safetensors weights in <repo>@<rev>`, `<repo> asks for trust_remote_code`,
    `the weights cache is full`, `free disk would fall below the floor`, `the Hub refused <repo>: <status>`.
  - `weights.evict(root, need, in_use)`: drops least-recently-used revisions (an `index.json` of last use) not in
    `in_use`; `in_use` is every `(repo, revision)` named by a `planned` or `running` note in the vault.
  - The token: the owner's `HF_TOKEN` from the broker's environment, else the Hugging Face login file under HOME
    (`.cache/huggingface/token`). It is passed to `http` only, never into the script's environment.
  - Broker: a request without `detach` for an experiment whose plan names models is refused
    `an experiment that names models runs detached`.
  - Runner: with models, the profile allows back `root/<repo>/<revision>/` for each model, to read only, and the
    script gets `SCOUT_LAB_WEIGHTS_DIR=<root>`. With `lab.python` set, argv `python3` runs that path, and its
    interpreter root is allowed back to read as the run's PATH interpreters are.
  - The run log lists every `Fetched` (repo, revision, file, sha256).

- [ ] **Step 1: Write the failing tests** (a fake `http` serves a fixture repo listing and bytes):
  - `test_fetch_takes_only_allowed_files`: a listing with `model.safetensors`, `config.json`, `tokenizer.json`,
    `modeling.py`, `pytorch_model.bin` and `x.pkl` fetches exactly the first three, with their sha256s.
  - `test_fetch_refuses_pickle_only` and `test_fetch_refuses_remote_code` (`config.json` with
    `"auto_map"` or `"trust_remote_code": true`): exact reasons above, nothing left in the cache.
  - `test_fetch_respects_the_floor` and `test_fetch_evicts_least_recently_used`, `test_fetch_never_evicts_in_use`.
  - `test_fetch_is_resumable_and_atomic`: an interrupted file leaves no file at its final name.
  - `test_token_never_reaches_the_script`: the script environment `run_experiment` builds has no `HF_TOKEN` and no
    value equal to the token.
  - `test_attached_run_with_models_is_refused`.
  - `test_profile_allows_the_named_revision_only`: `decide` gives read `allow` for `root/a/b/<rev>/x.safetensors`,
    `deny` for another revision of the same repo, and write `deny` for both.
  - Kernel: `test_kernel_script_reads_but_cannot_write_weights`: a script reads a cached file (exit 0) and its write
    there fails with `Operation not permitted`.
- [ ] **Step 2: Run them; they fail.**
  `.venv/bin/python -m pytest tests/unit/test_lab_weights.py tests/unit/test_lab_sandbox.py tests/unit/test_run_broker_experiment.py -q -k 'weights or fetch or models or token'`
- [ ] **Step 3: Implement.** No new engine dependency. The fetch runs in the supervisor before `run_experiment`,
  and a fetch error ends the experiment with outcome `not run: <reason>`.
- [ ] **Step 4: Run the Step 2 command with the kernel on, then `-k 'lab or broker'`.** All pass.
- [ ] **Step 5: Commit** `feat(lab): an owner-side weights cache, read-only to scripts, and the owner's Python`

### Task 9: ML experiments, part 5: the GPU (MPS)

**Files:**
- Modify: `plugin/engine/scout/lab/sandbox.py` (`GPU_RULES`, `experiment_profile(..., gpu: bool)`)
- Modify: `plugin/engine/scout/lab/runner.py` (passes `LabLimits.gpu`; points Metal's cache into the scratch dir)
- Modify: `docs/superpowers/specs/2026-11-21-research-lab-design.md` §14b (one row per rule)
- Create: `tests/fixtures/lab/mps_probe.py` (allocate a tensor on `mps`, a 512×512 matmul, `torch.mps.synchronize()`, print `mps ok`)
- Test: `tests/unit/test_lab_sandbox.py`, `tests/unit/test_lab_runner.py`

**Interfaces:**
- Consumes: `LabLimits.gpu`, `LabLimits.python` (Tasks 5 and 8).
- Produces: `GPU_RULES: tuple[str, ...]`, the SBPL lines rendered after the existing denies only when `gpu=True`;
  `gpu=False` renders today's profile byte for byte.

- [ ] **Step 1: Write the failing tests:**
  - `test_gpu_off_profile_is_unchanged`: the rendered profile equals today's for the same inputs.
  - `test_gpu_rules_open_no_files_or_network`: no rule in `GPU_RULES` allows `file-write*` outside the scratch dir,
    `network*`, `process-exec` or `mach-lookup` of a `DENIED_MACH` name.
  - Kernel, skipped unless `SCOUT_TEST_TORCH_PYTHON` names an interpreter with torch:
    - `test_kernel_mps_runs_with_gpu_on`: the probe prints `mps ok`.
    - `test_kernel_mps_fails_with_gpu_off`: the probe doesn't print `mps ok` (this proves the rules are what opened it).
    - `test_kernel_denials_hold_with_gpu_on`: with `gpu=True`, a write outside the folder, a direct TCP connection
      and a read of a private fixture file are each refused.
- [ ] **Step 2: Run them; the unit ones fail, and the kernel ones show which access MPS needs.**
  `SCOUT_TEST_TORCH_PYTHON=<owner venv python> .venv/bin/python -m pytest tests/unit/test_lab_sandbox.py tests/unit/test_lab_runner.py -q -k 'gpu or mps'`
- [ ] **Step 3: Find the rules.** Start from `GPU_RULES = ()`. Run the probe under the profile, read the sandbox's
  denial log (`log show --predicate 'sender == "Sandbox"' --last 2m`), add the one rule the first denial names,
  repeat. Stop when the probe passes. Record each rule in §14b with its operation, path and why. If MPS needs a
  write outside the scratch dir that an environment variable can't redirect, stop and record it as a ruling: it
  widens the sandbox.
- [ ] **Step 4: Run the Step 2 command, then `-k 'lab or broker'`.** All pass.
- [ ] **Step 5: Commit** `feat(lab): experiments may use the GPU (MPS) when the owner turns it on`

### Task 10: Research phases, part 1: choosing the run's work, and experiments

**Files:**
- Rewrite: `plugin/phases/research/research-targets.md`. Keep the frontmatter and
  `slot: target-selection`. The heading becomes `# PHASE 1: THE LAB — CHOOSE THIS RUN'S WORK`.
- Create: `plugin/phases/research/lab-experiments.md` with frontmatter `phase: research`,
  `name: Lab Experiments`, `slot: experiments`, `mode: [research]`, `requires: null`. The heading
  is `# PHASE 2E: EXPERIMENTS`.
- Test: `tests/unit/test_phase_lab_research.py` (new)

**Interfaces:**
- Consumes these commands, exactly as Plan 1 and Task 3 define them:
  - `lab list --json`, `lab show <slug> --json`;
  - `lab reply <slug> --on thread|Qn|En --to <n> --text`;
  - `lab question add|answer|drop`;
  - `lab propose --slug --title --question --open … [--evidence TARGET=WHY] [--visibility private]`;
  - `lab propose-close <slug> --reason`;
  - `lab experiment plan|start|run|finish|abandon`, `run --detach`, `collect` (Task 7);
  - the plan flags `--timeout-hours`, `--model REPO@REVISION`, `--disk-budget` (Task 5);
  - `lab experiment call --prompt-file` (inside scripts only).
- Produces: the phase text Task 11's assembly test reads.

**What research-targets.md must say**, in this order. The quoted phrases are pinned by the tests.

1. **Step 1a: read the lab block.**
   - Start from `.scout-cache/session-context.json` → `lab`.
   - Fall back to `scripts/scoutctl lab list --json` when `generated_at` isn't today or `lab` is
     missing or truncated.
   - Read only the threads you'll work, with `scripts/scoutctl lab show <slug> --json`.
   - Never list or read `knowledge-base/lab/` file by file.
   - Every lab write goes through `scripts/scoutctl lab`, which locks the thread for that one
     write; a hand edit can overwrite an owner comment made mid-run.
2. **Step 1b: "Owner comments come first".**
   - Cover every `active` or `paused` thread with `unanswered_comments` > 0, oldest comment
     first.
   - Each comment gets `lab reply`, whose `--to` is the comment's 1-based position in that
     target's `comments` list.
   - The reply says what was done: answered, planned an experiment, re-prioritised, or
     "declined, with the reason". A comment is never skipped because it is hard or out of scope.
   - A comment starting `Q:` becomes `lab question add <slug> --text … --by <author>`, and the
     reply names the new ID.
   - "A paused thread's comments are answered; the thread is not researched".
   - Dropping a question the owner raised or commented on needs `--owner-comment <n>`.
   - If the budget ends first, the rest wait. The wrap says how many.
3. **Step 1c: due experiments.** Run them next, per the Experiments phase.
4. **Step 1d: pick threads. "Work 1–2 threads in depth".**
   - Owner-prioritised threads come first. That means a thread-level comment asking for focus,
     until Scout's reply says it was handled.
   - Then coverage mandates. Keep today's optional `scripts/coverage-mandates.py` block, its
     exit codes and the "do not extend dates" rule. A live mandate makes its matching thread
     owner-prioritised. A mandate with no matching thread becomes a proposal.
   - Then the longest since `last_run`.
   - Skip threads out of scope for this vault (any scope rule in this file binds).
   - A private thread is worked from the KB alone, never as a web or connector query target.
5. **Step 1e: "No research outside threads".** With no `active` thread, a run answers comments
   and proposes, nothing else.
6. **Step 1f: propose.**
   - **When to propose:** the conditions in §6.1.
   - **Limits:** at most 2 per run (`lab.max_proposals_per_run`), and at most 5 open
     (`lab.max_open_proposals`; `lab propose` refuses beyond that).
   - **What a proposal holds:** 2–5 starting questions.
   - **Sources:**
     - a question in an active thread big enough to stand alone;
     - fewer active threads than `lab.target_threads`;
     - for an empty lab, today's entity-scoring priority list, kept verbatim with its
       `kb query --visibility normal` and 7-day skip.
   - **Privacy:** a proposal built from private material uses `--visibility private`.
   - **Close proposals:** `lab propose-close` for a thread with no open questions, or after
     `lab.stale_runs` runs without movement.
7. **Step 1g: refusals.**
   - If a `scoutctl lab` write is refused (the thread was paused, or its front page doesn't
     parse), stop working that thread.
   - Still reply to its comments if that is allowed.
   - Name it in the wrap, and "never edit a lab file by hand to get around a refusal".

**Remove:** the research-queue sections, `urgent` preemption, the graph-completeness rule and
the run-log continuity note (spec §7).

**What lab-experiments.md must say:**

1. **Running due experiments.**
   - Only experiments in `active` threads run; a paused thread's experiments wait.
   - **Collect detached experiments first.** For each `running` experiment the lab block marks
     `detached`, run `scripts/scoutctl lab experiment collect <slug> <EID>` and read its last
     line: `experiment ran:` → finish it as below; `experiment running:` → leave it and name it
     in the wrap; `experiment not run:` → `lab experiment abandon` with the reason.
   - Then retry attached `running` experiments left by an earlier run (ruling P2-e). Then take the
     `due` experiments not in `held`, while the run's allowance lasts (exit 5 = used).
   - A held experiment's comment was answered in Step 1b. Run it only if that comment doesn't
     object. If it objects, `lab experiment abandon` with the reason.
   - **Before running,** commit pending work: the broker is busy for the whole experiment.
   - **Start:** `lab experiment start <slug> <EID>`, then commit the note.
   - **Run:** `scripts/scoutctl lab experiment run <slug> <EID> --timeout 540 -- <python3|sh|bash|node> <script in the experiment folder>`,
     with the Bash tool timeout set to `600000`.
   - **Run detached** when the note sets `timeout_hours` or names `models`: the same command with
     `--detach` and without `--timeout`. `experiment started: detached` means it is running; leave
     it `running`, commit, and go on. A later run collects it. `experiment refused:` and
     `experiment not run:` read as below.
   - **Read the last stderr line:**
     - `experiment ran: exit <n>` → finish it, whatever `n` is; a non-zero exit is part of the
       result;
     - `experiment refused:` → `lab experiment abandon` with the refusal as the reason, and plan
       a new EID if the work is still wanted (ruling P2-g);
     - `experiment not run:` → leave it `running` and say so in the wrap.
   - **Finish:** `lab experiment finish` with `--result-file`, `--means-file` and `--one-line`.
     - The Result gives numbers and what was *not* shown.
     - A cap refusal from `lab experiment call` (exit 3) means a partial result.
     - A sandbox denial is a finding, never retried another way.
     - A negative or inconclusive result "is a result", scoped by method and date.
   - **Timeline:** `lab timeline add --tag experiment`.
   - **Commit:** the note and the experiment folder's outputs, by explicit path.
2. **Planning experiments** (while working a thread):
   - **When:** only when reading can't settle a question and the data is in bounds. Copy the
     spec §6.3 in-bounds and out-of-bounds lists.
   - **Private threads:** "No experiments in private threads" (ruling P2-a).
   - **Body:** write `## Hypothesis`, `## Method` (the exact command) and `## Data` to a temp
     file, then run `lab experiment plan <slug> --short … --title … --question Qn --file … [--cap …]`.
   - **Long or ML experiments:** set `--timeout-hours` for anything over 9 minutes, `--model`
     with a full 40-character commit hash for each set of weights (safetensors only), and
     `--disk-budget` when outputs may pass 512 MiB. Scripts read weights from
     `$SCOUT_LAB_WEIGHTS_DIR`, write large files only into `checkpoints/` (never committed), and
     list each checkpoint's path, size and sha256 in the Result.
   - **Scripts:** write them into `knowledge-base/lab/<slug>/experiments/<slug>-<EID>-<short>/`.
     They read their own files through `$SCOUT_LAB_SOURCE_DIR`, write outputs in their working
     directory, and make model calls only through `scripts/scoutctl lab experiment call --prompt-file`.
   - **Commit** the note and scripts together.
   - "Never edit a planned experiment's note or scripts after committing them". An edit restarts
     the 6 h notice (`lab.experiment_notice_hours`).
   - **Vetoes:** never re-plan a vetoed experiment under a new ID unless an owner comment invites
     it.
   - **Scout changes:** a result that suggests changing Scout goes to the research→Scout
     findings path, never into a Scout edit.

- [ ] **Step 1: Write the failing tests** in `test_phase_lab_research.py`, in the style of
  `test_phase_content_shape.py`:
  - `test_targets_phase_pins_the_run_order`: every phrase quoted above appears in
    research-targets.md. "Owner comments come first" appears before "Work 1–2 threads in
    depth".
  - `test_targets_phase_drops_the_queue`: none of `research-queue/`, `research_queue`,
    `priority: urgent` or `graph-completeness` appears.
  - `test_experiments_phase_pins_the_rules`: `--timeout 540`, `600000`, the three status-line
    prefixes, `--detach`, `experiment started: detached`, `experiment running:`,
    `scoutctl lab experiment collect`, `$SCOUT_LAB_WEIGHTS_DIR`, `checkpoints/`, "No experiments in private threads", "Never edit a planned experiment's note or
    scripts after committing them", `$SCOUT_LAB_SOURCE_DIR`, and "is a result".
  - `test_both_phases_are_research_only`: `parse_phase_file` gives `mode == ["research"]` for
    each section.
  - `test_every_lab_command_named_exists`: extract every `scoutctl lab <word>( <word>)?` from
    both files. For each, `CliRunner().invoke(scout.lab.cli.app, [...words, "--help"])` exits 0.
- [ ] **Step 2: Run them; they fail.** `.venv/bin/python -m pytest tests/unit/test_phase_lab_research.py -q`
- [ ] **Step 3: Write the two phase files.** Keep research-targets.md under 9 KB and
  lab-experiments.md under 9 KB. Write for a cold reader, and gloss each identifier on first use.
- [ ] **Step 4: Run the Step 2 command, plus `test_phase_privacy_rule.py` and
  `test_phase_explicit_path_commits.py`.** All pass.
- [ ] **Step 5: Commit** `feat(research): runs choose work from the lab: comments, experiments, threads, proposals`

### Task 11: Research phases, part 2: integration, commit and wrap; assembly check

**Files:**
- Modify: `plugin/phases/research/deep-research.md`. Step 2a reads the thread's front page and
  the notes in its Evidence, and a research target is a thread's open question. Steps 2b and 2c
  are unchanged.
- Modify: `plugin/phases/research/knowledge-integration.md`. Add **Step 3d: Update the thread**,
  and extend Step 3c.
- Modify: `plugin/phases/research/commit-notify.md` (Steps 4a and 4b, and both Step 4d
  notification sections)
- Test: `tests/unit/test_phase_lab_research.py` (extend), `tests/unit/test_phase_assembly.py`
  (extend)

**What the text must say:**

- **Step 3d: update the thread,** after the topic notes:
  - `lab understanding set <slug> --file …`: the whole section, kept under 2 KB;
  - `lab question answer|drop|add`;
  - `lab evidence add --target … --why …` for each topic note used;
  - one `lab timeline add <slug> --tag "research run" --did … --found … --questions … [--steers …]`
    per touched thread. Each field is "a sentence a cold reader understands", never a commit
    subject or a file list.
- **Step 3c** keeps "Do NOT create action items". It adds: findings that suggest changing Scout
  go to the research→Scout findings path.
- **Step 4a:**
  - the commit subject is `research [HH:MM]: <threads worked and what moved>`;
  - the lab files go in the explicit path list.
- **Step 4b:** the session-log sentence names threads, not entities.
- **Step 4d** (Slack and Telegram alike):
  - **Gather:** run `scripts/scoutctl lab list --json` at wrap time.
  - **Threads:** one line per touched thread (e.g. "Q3 now has a planned experiment; Q1
    answered").
  - **Approvals:** proposals "awaiting your approval".
  - **Comments still waiting:** the sum of `unanswered_comments` over `active` and `paused`
    threads, worded "comments still waiting".
  - **Experiments:** each one run, any left `not run`, each detached one still running (with its
    elapsed time), and the bytes held in checkpoints across the lab.
  - **Privacy:** Slack names only normal-visibility threads ("N private threads also moved" is
    allowed). Telegram may name private threads by title, as today.

- [ ] **Step 1: Write the failing tests.**
  - `test_integration_and_wrap_pin_the_thread_updates`: knowledge-integration.md contains
    `--tag "research run"` and "a sentence a cold reader understands". Both notification
    sections contain "comments still waiting" and "awaiting your approval". The Slack section
    contains "only normal-visibility threads".
  - `test_research_brain_assembles_with_the_lab`: use the `BootstrapConfig` setup
    `test_phase_assembly.py` already uses, with `plugin_root` = the repo's `plugin/` and
    `enabled_connectors={"slack", "telegram"}`. `_assemble(cfg, "RESEARCH")`:
    - contains `PHASE 1: THE LAB`, `PHASE 2E: EXPERIMENTS` and `scoutctl lab experiment run`;
    - contains neither `research-queue/` nor `research_queue`.
    `_assemble(cfg, "SKILL")` and `_assemble(cfg, "DREAMING")` contain neither phase heading.
    Grep rendered text: `{{USER_NAME}}` is rendered.
  - `test_run_sandbox_allows_lab_writes`: `profile.decide(profile.build_rules(vault, env), path)`
    is `"allow"` for `<vault>/.scout-state/lab.lock` and
    `<vault>/knowledge-base/lab/t/t.md`.
- [ ] **Step 2: Run them; they fail.**
  `.venv/bin/python -m pytest tests/unit/test_phase_lab_research.py tests/unit/test_phase_assembly.py -q`
- [ ] **Step 3: Edit the three phase files.** Keep each phase file's existing frontmatter and
  slot.
- [ ] **Step 4: Run the Step 2 command, then `-k phase`.** All pass.
- [ ] **Step 5: Commit** `feat(research): thread updates, timeline entries and a lab wrap`

### Task 12: Changelog, full suite, draft PR

**Files:**
- Create: `plugin/changelog.d/lab-plan-2.changed.md`, with two bullets: research runs now work
  the Lab (comments first, experiments, threads, proposals, wrap); and the heartbeat fires
  research on lab state. No vault content.

- [ ] **Step 1: Run the full suite and gates.**
  `.venv/bin/python -m pytest -n auto -q && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy scout/lab scout/sandbox`
  Expected: everything passes except tests the ledger records as already failing at the
  merge-base. The kernel tests ran.
- [ ] **Step 2: Run the changelog check.** `.venv/bin/python -m scout.scripts.changelog check`
  exits 0.
- [ ] **Step 3: Commit** `docs(lab): changelog fragment for Plan 2`
- [ ] **Step 4: After the final review and its fix wave, open the draft PR.**
  - Push `feat/lab-plan-2` and open a draft PR on Raven-Scout/Scout, base
    `docs/research-lab-design`.
  - Title: `feat(research): the Lab, Plan 2 — runs work threads, comments and experiments`.
  - Body:
    - link the spec and this plan;
    - list the rulings P2-a to P2-g;
    - list the Review Focus items and how each is tested;
    - state that it merges together with Plans 3 and 4;
    - end with the Claude Code attribution line.
