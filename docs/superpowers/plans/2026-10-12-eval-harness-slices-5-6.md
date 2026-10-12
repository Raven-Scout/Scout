# Eval harness, slices 5–6: more checks, connector-down, #372 demotion

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:**
- **Slice 5 (PR A):** add the remaining §6.1 checks and the `connector-down` scenario.
- **Slice 6 (PR B):** add the #372 demotion check, with seeds for its three branches.

**Architecture:**
- Each check is a pure function in `harness/checks/`. The runner reads the inputs: the post-run
  daily file, the context notes, the captured wrap DM, `review-queue.md` before and after, and the
  calls log.
- A scenario lists its checks and their expectations in `scenario.yaml`.
- No new model-backed machinery.

**Spec:** `docs/superpowers/specs/2026-10-12-eval-harness-design.md` §6.1, §7.1–7.3. Phase rules
come from `plugin/phases/core/action-items.md` on main:
- *Past-Due Tasks Step Down* (:67-78);
- the dropoff audit (:306-312);
- the body rule (:185);
- context notes;
- 🟢 waiting lines;
- `00-run-modes.md` *Connector Outages* (:34-43).

## Global constraints
- No API key. Live runs are N=1 on Sonnet, and the launchctl/crontab stubs stay first on PATH.
- CI is tier 0 only. Checks are pure and covered by the purity test.
- Fixtures are hand-written and anonymized, and the scrub test must pass.

## Review focus
1. **A check that passes on an untouched carried-forward file.** It must read `not_enriched`; the
   runner already short-circuits this, so a test pins it per new check.
2. **Outage text that names a connector only in passing**, e.g. "Linear: 3 issues". It must not
   count as an outage claim, and a real claim must.
3. **A scenario whose missing tags exist in yesterday's file but not in the pre-seeded today
   file.** The fixture's own consistency is tested.
4. **A context-note filename that differs from the tag in case.** The match is exact
   (`<TAG>.md`).

---

## PR A: slice 5

### Task A1: Checks over the daily file
Each check gets golden tests (good and bad text).
- **`context_note(today, yesterday, notes)`.** Every open tag new today has a note whose
  frontmatter carries `tag`, `title`, `created` and `updated`.
- **`waiting_format(today, expected)`.** Each expected waiting item (a tag or `ref:`) has a body
  matching `waiting on <who> since <date>`.
- **`no_collapse(today)`.** No open row's title or body summarises several items (`etc.`,
  `and N more`, `+N more`).
- **`body_budget(today)`.** The body after ` — ` has at most 40 words. Over 25 words is reported in
  the details but still passes.

### Task A2: Checks over the run's outputs
- **`outage_reported(down, wrap, run_notes, calls)`.** For each persona scripted as down:
  - the wrap names it with an outage word;
  - so do the run notes;
  - `calls` has at least one call to it.
- **`no_false_outage(up, wrap, run_notes)`.** No persona that answered is named with an outage
  word.
- **`dropoff_caught(missing, today, wrap, review_added)`.** Each missing tag is back in today's
  file with `[carried-via-audit]`, the wrap says "silently dropped", and `review-queue.md` gained
  text.

### Task A3: Runner wiring
The runner gathers each check's inputs:
- notes from `action-items/context/*.md`;
- the wrap from `calls.jsonl` captures (the `slack_send_message` text);
- the run notes from today's 🪵 section;
- `review-queue.md` diffed against the fixture commit;
- per-persona call counts.

`scenario.yaml` gains `expected_waiting`, `personas_down` and `dropped_tags`. `busy-weekday`
adds `context_note`, `waiting_format`, `no_collapse` and `body_budget`.

### Task A4: The `connector-down` scenario
- **Personas:** Slack, Linear, Calendar and Email. Linear's `"*"` rule returns `auth_expired`;
  Email's returns `unavailable`.
- **Seeds:** yesterday's file, plus a pre-seeded today file (an earlier run's, no banner) that is
  missing two of yesterday's open tags.
- **Checks:** `outage_reported`, `no_false_outage`, `dropoff_caught`, `no_drop`.
- **Tests:** fixture consistency, and that the scrub passes.

### Task A5: One live sample each (N=1 Sonnet) for `busy-weekday` and `connector-down`
Record the results in spec §7. Then review, open the PR and send the number.

## PR B: slice 6

### Task B1: `demotion(today, yesterday, notes, expected)`
`expected` maps a tag to a branch:
- **`hold`:** under 7 quiet days. The item stays in its section and its body is unchanged.
- **`step_down`:** at most 🟡; the body has `due <date>; still want this?`; the note has a dated
  `stepped down, past due with no news` line.
- **`park`:** the item is in 🅿️ Parked, with its text byte-identical to yesterday's.

Golden tests cover each branch.

### Task B2: Seeds in `busy-weekday`
- `OVRD` (step_down) is already there; its note is created by the run.
- `LATE`: due `{{d-2}}`, Alex's move, so `hold` in 🔴.
- `PARK`: stepped down `{{d-16}}` per its seeded note, no news since, so `park`.

Live N=1, record, review, open the PR and send the number.
