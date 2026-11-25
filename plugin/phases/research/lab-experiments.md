---
phase: research
name: Lab Experiments
slot: experiments
mode: [research]
requires: null
---

# PHASE 2E: EXPERIMENTS

An experiment (its EID is `E1`, `E2` … within a thread) is a note plus a folder `knowledge-base/lab/<slug>/experiments/<slug>-<EID>-<short>/` for its scripts and outputs. It goes `planned` → `running` → `done` or `abandoned`, and the owner can veto it. Scripts run only through the run's **broker** (a process outside this run's sandbox that runs them in a narrower one). Each answer ends in one stderr status line, read as below.

"Commit" here means `{{SCOUT_DIR}}/scripts/git-safe-commit.sh "research [HH:MM]: <what>" <explicit paths>`.

## Running due experiments

Only experiments in `active` threads run; a paused thread's experiments wait. Take them in this order while the run's allowance lasts (exit 5 means this run's experiments are used: stop running, keep planning).

1. **Collect detached experiments first.** For each `running` experiment whose `running_detail` row in the lab block says `detached: true`, run `scripts/scoutctl lab experiment collect <slug> <EID>` and read its last line:
   - `experiment ran: exit <n>` → finish it, as below.
   - `experiment running: <elapsed> of <deadline>` → leave it, and name it in the wrap with its elapsed time.
   - `experiment not run: <reason>` → its final outcome (the supervisor stopped without a result, it ran past its deadline, the owner stopped it, or a weights fetch was refused). Run `lab experiment abandon <slug> <EID> --reason "<reason>"`. One exception: `a process the experiment started survived the sweep; check ps` is not final. Leave it `running` and name it in the wrap; the next collect retries.
2. **Then retry attached `running` experiments** an earlier run left unrun: run them as below, without `start`.
3. **Then the `due` experiments not in `held`.** A held experiment's comment was answered in Step 1b. Run it only if that comment doesn't object. If it objects, `lab experiment abandon` with the reason.

For each one:

- **Before running,** commit pending work: the broker is busy for the whole experiment, and it refuses an experiment with uncommitted changes.
- **Start:** `scripts/scoutctl lab experiment start <slug> <EID>`, then commit the note.
- **Run,** with the Bash tool timeout set to `600000`:

  ```bash
  scripts/scoutctl lab experiment run <slug> <EID> --timeout 540 -- <python3|sh|bash|node> <script in the experiment folder>
  ```

- **Run detached** when the note sets `timeout_hours` or names `models`: the same command with `--detach` and without `--timeout`. `experiment started: detached` means it is running: leave it `running`, commit, and go on. A later run collects it. Any other answer reads as below.
- **Read the last stderr line:**
  - `experiment ran: exit <n>` → finish it, whatever `n` is. A non-zero exit is part of the result.
  - `experiment refused: <reason>` → the broker refused this experiment (notice not over, vetoed, uncommitted changes, a bad command or note, or an attached run of one that names models). Run `lab experiment abandon <slug> <EID> --reason "<reason>"`, and plan a new EID if the work is still wanted.
  - `experiment not run: <reason>` → nothing ran, and the experiment isn't at fault: experiments or detached runs are off in the owner's config, a detached run is already running or not yet collected, or free disk would fall below the floor. Leave it `running` and say so in the wrap; a later run retries it. When experiments are off, still plan experiments.
- **Finish:** write the Result and What it means to temp files, then `scripts/scoutctl lab experiment finish <slug> <EID> --result-file <file> --means-file <file> --one-line "…"`.
  - The Result gives numbers, and what was *not* shown.
  - A cap refusal from `lab experiment call` (exit 3) means a partial result: say so.
  - A sandbox denial (a refused write or connection) is a finding, never retried another way.
  - A negative or inconclusive result is a result, scoped by method and date.
- **Timeline:** `lab timeline add <slug> --tag experiment --did "…" --found "…" --questions "…"`.
- **Commit** the note and the experiment folder's outputs by explicit path, never `checkpoints/`. The commit refuses any file over 10 MiB under `knowledge-base/lab/`.

Never call `scripts/scoutctl lab experiment stop`: stopping a running experiment is the owner's.

## Planning experiments

While working a thread, plan an experiment only when reading can't settle a question and the data is in bounds.

- **In bounds:** reading the vault (KB, transcripts under the vault's session data, `.scout-logs`, the mistake audit, connector call logs); running local scripts; model calls within the cap; writing only inside the experiment's note and its folder.
- **Out of bounds:** posting or sending anything; writing to connectors or external systems; editing any other vault file; changing Scout's own instructions, config or code; downloading or executing third-party code (weights the owner's side fetches are data); pulling private material into web or connector queries.
- **No experiments in private threads** (the broker refuses them).
- **Body:** write `## Hypothesis`, `## Method` (the exact command) and `## Data` to a temp file, then:

  ```bash
  scripts/scoutctl lab experiment plan <slug> --short <short> --title "…" --question Qn --file <tmp> [--cap <usd>]
  ```

  It prints `ok <slug> plan <EID>`. `--short` is a few words for the folder name. `--cap` is the cost cap in USD (default `lab.experiment_cost_cap_usd`, $1.00; the owner's maximum binds).
- **Long or ML experiments:** set `--timeout-hours H` for anything over 9 minutes. Set `--model REPO@REVISION` for each set of weights, with a full 40-character commit hash as the revision (safetensors only). Set `--disk-budget BYTES` when outputs may pass 512 MiB (the owner's maximum binds). Scripts read weights from `$SCOUT_LAB_WEIGHTS_DIR` (read-only), write large files only into `checkpoints/` in their folder (never committed), and list each checkpoint's path, size and sha256 in the Result.
- **Scripts:** write them into `knowledge-base/lab/<slug>/experiments/<slug>-<EID>-<short>/`. A script reads its own committed files through `$SCOUT_LAB_SOURCE_DIR` and writes outputs in its working directory. Committed outputs can't be rewritten, so a re-run writes new names and reads earlier outputs from `$SCOUT_LAB_SOURCE_DIR`. Model calls go only through `scripts/scoutctl lab experiment call --prompt-file <file>`.
- **Commit** the note and scripts together, by explicit path.
- Never edit a planned experiment's note or scripts after committing them. An edit restarts the 6 h notice (`lab.experiment_notice_hours`).
- **Vetoes:** never re-plan a vetoed experiment under a new ID unless an owner comment invites it.
- **Scout changes:** a result that suggests changing Scout goes to the research→Scout findings path, never into a Scout edit.
