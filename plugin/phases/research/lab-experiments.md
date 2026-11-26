---
phase: research
name: Lab Experiments
slot: experiments
mode: [research]
requires: null
---

# PHASE 2E: EXPERIMENTS

You reach this phase from Step 1c (PHASE 1). Experiments (`E1`, `E2` …) live in `knowledge-base/lab/<slug>/experiments/`. Only the run's **broker** runs scripts, in a narrower sandbox than this run's; each answer ends in one stderr status line.

Commits use `{{SCOUT_DIR}}/scripts/git-safe-commit.sh` with explicit paths (Step 4a).

## Running due experiments

A run runs at most `lab.experiments_per_run` (default 1) experiments, a detached start included; exit 5 means that is used. In order:

1. **Collect detached experiments first,** in `active` and `paused` threads (collect writes no lab file). For each `running` one marked `detached: true` in the lab block's `running_detail`, run `scripts/scoutctl lab experiment collect <slug> <EID>` and read its last line. In a paused thread, only report the result in the wrap: `finish` and `abandon` wait until the thread resumes (Step 1g).
   - `experiment ran: exit <n>` → finish it, as below.
   - `experiment running: <elapsed> of <deadline>` → leave it; name it and its elapsed time in the wrap.
   - `experiment not run: <reason>` → `lab experiment abandon <slug> <EID> --reason "<reason>"`, unless the reason is exactly one of `this run's broker didn't answer`, `collecting a detached experiment needs the run broker` or `a process the experiment started survived the sweep; check ps`: then leave it `running` and name it in the wrap; the next collect retries. Never re-plan an abandoned `a veto can't be ruled out` (**Vetoes**).
2. **Then retry `running` experiments not marked `detached`** in `active` threads (left unrun earlier): run them as below, without `start`. Before retrying a `running` experiment, read the owner comments on its line and on its questions in `lab show`; if one objects, abandon it with the reason.
3. **Then the `due` experiments,** in `active` threads. Run a `held` one only if its comment, answered in Step 1b, doesn't object; else abandon it with the reason.

For each one:

- **Before running,** commit pending work: the broker is busy throughout and refuses uncommitted changes.
- **Start:** `scripts/scoutctl lab experiment start <slug> <EID>`, then commit the note.
- **Run,** with the Bash tool timeout at `600000`:

  ```bash
  scripts/scoutctl lab experiment run <slug> <EID> --timeout 540 -- <python3|sh|bash|node> <script in the experiment folder>
  ```

- **Run detached** when the note sets `timeout_hours` or names `models`: the same command with `--detach`, without `--timeout`. `experiment started: detached` → leave it `running`, commit and go on; a later run collects it.
- **Read the last stderr line:**
  - `experiment ran: exit <n>` → finish it, whatever `n` is: a non-zero exit is part of the result.
  - `experiment refused: <reason>` → with exit 5 (`this run has run its …`) the allowance is used, which is not a refusal of the experiment: stop running experiments this run and leave that one as it is. Otherwise:
    - `held:` (an unanswered owner comment): answer it per Step 1b now; if the comment objects, `lab experiment abandon` it with the reason now; otherwise leave it `running` for the next run.
    - a veto (any reason containing `vetoed` or `a veto can't be ruled out`): leave it, and never re-plan it unless an owner comment invites it (see **Vetoes**).
    - anything else (notice not over, uncommitted changes, a bad command or note, models on an attached run): run `lab experiment abandon <slug> <EID> --reason "<reason>"`, and plan a new EID if the work is still wanted.
  - `experiment not run: <reason>` → nothing ran (no broker to run it, a switch off or `lab.python` missing in the owner's config, a detached run uncollected, the disk floor): leave it `running`, say so in the wrap; a later run retries. When experiments are off, still plan them.
- **Finish:** write the Result and What it means to temp files, then `scripts/scoutctl lab experiment finish <slug> <EID> --result-file <file> --means-file <file> --one-line "…"`.
  - The Result gives numbers, and what was *not* shown. A `killed: <flag>` line before the status line (memory, disk floor, bytes, processes, timeout, a stop) goes in the Result. It cites each model's repo, revision and file sha256s from the run log.
  - A cap refusal from `lab experiment call` (exit 3) means a partial result.
  - A sandbox denial (a refused write or connection) is a finding, never retried another way.
  - A negative or inconclusive result is a result, scoped by method and date.
- **Timeline:** `lab timeline add <slug> --tag experiment --did "…" --found "…" --questions "…"`.
- **Commit** the note and the experiment folder's outputs by explicit path, never `checkpoints/`. The commit refuses any file over 10 MiB under `knowledge-base/lab/`.

Never call `scripts/scoutctl lab experiment stop`: stopping a running experiment is the owner's.

## Planning experiments

While working a thread, plan an experiment only when reading can't settle a question and the data is in bounds.

- **In bounds:** reading the vault; local scripts; model calls within the cap; writes only in the experiment's note and folder.
- **Out of bounds:** sending or posting; external writes; other vault files; Scout's own instructions, config or code; third-party code (owner-fetched weights are data); private material in web or connector queries.
- **No experiments in private threads.**
- **Body:** write `## Hypothesis`, `## Method` (the exact command) and `## Data` to a temp file, then:

  ```bash
  scripts/scoutctl lab experiment plan <slug> --short <short> --title "…" --question Qn --file <tmp> [--cap <usd>]
  ```

  It prints the new EID. `--short` names the folder; `--cap` is the USD cost cap (default `lab.experiment_cost_cap_usd`, $1.00; the owner's maximum binds).
- **Long or ML experiments:** `--timeout-hours H` for anything over 9 minutes; `--model REPO@REVISION` per set of weights, the revision a full 40-character commit hash (safetensors only); `--disk-budget BYTES` when outputs may pass 512 MiB (the owner's maximum binds). Scripts read weights from `$SCOUT_LAB_WEIGHTS_DIR` (read-only), write large files only into `checkpoints/` in their folder (never committed), and list each checkpoint's path, size and sha256 in the Result.
- **Scripts:** write them into the experiment's folder. A script reads its committed files through `$SCOUT_LAB_SOURCE_DIR` and writes outputs in its working directory, under new names on a re-run (committed outputs can't be rewritten). Model calls go only via `scripts/scoutctl lab experiment call --prompt-file <file>`.
- **Commit** the note and scripts together.
- Never edit a planned experiment's note or scripts after committing them. An edit restarts the 6 h notice (`lab.experiment_notice_hours`).
- **Vetoes:** never re-plan a vetoed experiment under a new ID unless an owner comment invites it.
- **Scout changes:** a result that suggests changing Scout goes to the research→Scout findings path, not a Scout edit.
