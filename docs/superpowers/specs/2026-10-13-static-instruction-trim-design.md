# Static-instruction trim (#84) — design

**Status:** Phase A built 2026-10-21 (§11); Phase B skipped (decision 2026-10-13) · issue Raven-Scout/Scout#84
**Scope:** `plugin/` only (assembly, runner template, eval harness). No `phases/` rule text changes in Phase A.

## 1. Headline: what this can and cannot buy

All numbers come from `.scout-logs/session-tokens.jsonl` (rows deduped to one per
`session_id`, using the row with the highest total). They use the logged
`cost_usd`, which is API-price-equivalent usage, as a stand-in for weekly-limit
consumption. Tokens are estimated as chars/4.

| Week | Scheduled Scout runs' share of logged usage |
|---|---|
| 09-15 → 09-21 | 11 % |
| 09-22 → 09-28 | 23 % |
| 09-29 → 10-05 | 20 % |
| **10-06 → 10-12 (limit exhausted)** | **8 %** (41 runs; interactive sessions were 90 %) |

Inside one scheduled briefing or consolidation run (Opus, median about 176 turns), the cost splits like this.
The split uses the per-token prices that reproduce the logged `cost_usd` exactly:

- **cache reads ≈ 72 %**, cache creation ≈ 15 %, output ≈ 12 %;
- the average resident context is about **194k tokens per turn**, and the live
  `SKILL.md` (about 47k tokens) is about **24 %** of it;
- so the brain file accounts for roughly **17–20 % of a scheduled run's usage**,
  because it is re-read from cache on every turn.

**Consequences**

1. Cutting the brain file by 40 % saves about 7–8 % per scheduled run. That is
   about 0.6–1.8 % of a week's usage. #84 is worth doing, but **it will not prevent
   another exhaustion on its own.**
2. Larger levers exist. They are out of scope here and flagged for separate issues (§9):
   - interactive sessions, about 90 % of the exhaustion week;
   - the hard-coded `--model opus` in `run-scout.sh.tmpl`;
   - consolidation cadence (4 runs a day);
   - tool-result context growth, the other ~150k of the 194k average.
3. The reason #84 was parked was "lazy loading doesn't reduce *peak* context".
   That is the wrong metric for usage. Usage is Σ(resident context × turns), so a
   section that is first read at turn 150 of 176 costs about 15 % of what it costs
   when read at turn 1, even though it stays resident afterwards. **Deferral does
   help briefing and consolidation, as long as the read happens late.**

## 2. Measurements

### 2.1 How the brain files are built

`bootstrap._assemble()` (`plugin/engine/scout/scripts/bootstrap.py`) globs the
`phases/` directories and keeps a section when `select_sections()` accepts it
(`phase_assembly.py`). The filters are:

- `requires:` — a connector, so assembly is **already connector-aware**;
- `mode:` — the target's mode set.

The targets are built from:

- `SKILL.md`: `core/` + `connectors/`, for `{briefing, consolidation}`;
- `DREAMING.md`: `core/` + `modes/`;
- `RESEARCH.md`: `core/` + `research/`.

Custom connector sections are appended. Every briefing and consolidation slot
reads the **same** `SKILL.md` in full ("Step 1: Read SKILL.md in full" in
`run-scout.sh.tmpl`). The Run Modes table (`core/00-run-modes.md`) then routes the
run, but every other mode's sections stay resident anyway.

### 2.2 Size per mode

Assembled from plugin `main` (0932cdd) with all 9 shipped connectors enabled.

| Run mode | Brain file loaded | Sections that mode executes | Loaded but unused by this mode |
|---|---:|---:|---|
| morning-briefing | SKILL 40.3k | 36.8k | 3.5k: 5 outbound scans 2.9k, post-meeting processing 0.4k, Monday preview 0.2k |
| weekend-briefing | SKILL 40.3k | ≈ 28k | about 12k: the above plus about 8.7k of heavy-connector scan detail that Weekend Scope tells the run to skip or abbreviate |
| *-consolidation (×4) | SKILL 40.3k | 36.6k | 3.8k: 8 query sections 3.0k, meeting-prep generation 0.5k, Monday preview 0.2k |
| dreaming | DREAMING 17.0k | 17.0k | — (feedback-processing already drops without Slack) |
| research | RESEARCH 4.7k | 4.7k | — |

These are the biggest sections. Shared means the section is loaded in every
target listed.

| Section | ~tok | Where it loads |
|---|---:|---|
| `core/action-items` | **12.9k** (32 % of SKILL) | SKILL |
| `modes/kb-deep-work` | 5.4k | DREAMING |
| `core/kb-management` | 4.9k | SKILL, DREAMING |
| `modes/feedback-processing` | 2.8k | DREAMING |
| `connectors/github` (5 sections) | 4.1k | SKILL |
| `connectors/slack` (6 sections) | 3.2k | SKILL |
| `core/git-setup` | 1.4k | all three |

Within `action-items`, the biggest blocks are:

- Board Fields: 1.4k;
- Knowledge Graph Personal Tasks: 0.8k;
- the Stable `[#TAG]` rule: 0.7k;
- Leave-State Compose Gate and Stale Auto-Archive: 0.66k each.

Paragraphs that cite dates, issue numbers or incidents ("history") total only
about **2.9k** across all phases. Of that, 1.0k is in action-items and 0.8k is
in feedback-processing.

### 2.3 The maintainer's vault (sizes only)

| | Live | Plugin snapshot (`.scout-state/last-assembled`) |
|---|---:|---:|
| `SKILL.md` | 47.2k | 39.1k |
| `DREAMING.md` | 23.4k | 17.0k |
| `RESEARCH.md` | 6.3k | 4.8k |

The live `SKILL.md` has been restructured into per-mode blocks:

- briefing-only blocks ≈ 11.6k;
- consolidation-only blocks ≈ 15.6k.

Mode slicing would therefore save 25–33 % on this vault, three times the plugin
figure. But the restructured file will not carry plugin-generated markers (§4.3),
so it gets nothing from Phase A until the vault owner marks it.

## 3. Ranked wins

| # | Win | Est. saving | Behaviour risk | Phase |
|---|---|---|---|---|
| W1 | **Mode-sliced brain view**: drop sections whose `mode:` excludes the run | 8–9 % of SKILL per run (plugin); 25–33 % on a marked custom vault | Low: uses the same `mode:` frontmatter that already filters DREAMING and RESEARCH | A |
| W2 | **Weekend slice**: also drop heavy-connector scan detail on `weekend-briefing` | ~20 % more on weekend runs (≈ 2 runs a week) | Medium: Weekend Scope allows a "quick check", so the slice keeps inbound headers | B |
| W3 | **Late-step deferral**: end-of-run sections become a pointer plus a cache file read when the run reaches them | ≈ 6–8 % of brain token-turns | Medium: the model must follow the pointer, and the pre-commit audits live here | B |
| W4 | **History out of rule text**: dated provenance moves to a non-assembled `phases/NOTES.md`; a one-line *why* stays | ≈ 2–3k across targets (≈ 5 %) | Low–medium: rationale helps adherence, so keep the one-line why | B |
| — | Connector-aware assembly | already done (`requires:`) | — | — |
| — | Deduplicate Scout Digest (action-items vs kb-deep-work) | < 0.3k | — | not worth it |

Combined target for the plugin assembly:

- **A:** −8–9 % per briefing or consolidation run;
- **A+B:** −20–25 % of brain token-turns for briefing and consolidation, about
  −35 % for weekend, about −5 % for dreaming and research.

## 4. Approach — Phase A (W1)

### 4.1 Markers in the assembly

`_assemble()` wraps each SKILL section in a pair of HTML comment lines when its
`mode:` is a strict subset of the target's modes:

```
<!-- scout:only briefing -->
…section body…
<!-- /scout:only -->
```

The markers are generated from existing frontmatter, so no `phases/` file
changes. They render invisibly in Obsidian. They are added at assembly time, not
written into phase files, so `parse_phase_file` is unaffected. DREAMING and
RESEARCH get no markers: they are already single-mode.

### 4.2 Slicing at run time

A new `scoutctl brain slice --kind SKILL --slot <slot-key>` reads the **live**
`SKILL.md`, so vault edits flow through, and writes `.scout-cache/brain/SKILL.md`.

- **Slot to mode class:**
  - `morning-briefing` and `weekend-briefing` → `briefing`;
  - `*-consolidation` → `consolidation`;
  - `manual`, an unknown slot, or no slot → **no slicing** (a manual run derives its mode inside the session).
- **What it writes:** it keeps the unmarked text and the blocks whose class
  matches, drops the others, and strips the markers. It prints one line,
  `brain slice: SKILL 40.3k → 36.8k tok (briefing)`, to the run log.
- **Fail-safe:** if a marker is unbalanced, nested or unknown, if a file is
  unreadable, or if anything raises, it copies `SKILL.md` verbatim (today's
  behaviour) and logs why. It never fails the run.

`run-scout.sh.tmpl` calls the slicer next to the existing pre-session hooks. The
prompt's Step 1 becomes "Read `$BRAIN` in full". `$BRAIN` is the slice when it
exists and `SKILL.md` otherwise. The prompt adds one line: "`SKILL.md` is the
file to edit; the slice is a read-only view." That matters for dreaming-applied
rule edits and for proposals.

### 4.3 How upgrades merge

- The markers are **pure line insertions at section boundaries**. On
  `bootstrap upgrade`, a vault that never edited SKILL.md fast-forwards, and a
  lightly edited vault 3-way merges cleanly unless an edit sits right on a
  boundary.
- A restructured vault (the maintainer's) gets a one-time
  `SKILL.md.proposed-merge` sidecar through the existing M3 guard and resolve
  flow. Without markers its slicer falls back to the whole file, so behaviour
  does not change and nothing is saved.
- Adopting markers in a grafted vault is the vault owner's choice. Agents never
  edit `~/Scout`.
- `phase_backport` must **strip markers** from both the snapshot and the live
  file before diffing, so markers never back-port into `phases/`. A tier-0 test
  covers this.
- `bootstrap drift` needs no change. The markers are ordinary lines in the snapshot.

## 5. Approach — Phase B (W2–W4, only if Phase A evals are clean)

The same marker mechanism gains two kinds:

- **`<!-- scout:only weekend -->`** (W2): a finer class for the heavy-connector
  `query`, `cross-check` and `update` sections. `weekend-briefing` drops them;
  `morning-briefing` keeps them. This needs a `weekend` value in the frontmatter
  `mode:` lists of those sections. That is a `phases/` change, and it gets an eval seed.
- **`<!-- scout:defer "<step name>" -->`** (W3): the slicer moves the block to
  `.scout-cache/brain/deferred/<slug>.md` and leaves a single line in its place:
  *"When you reach <step name>, Read `<path>` in full before doing it."*
  `SKILL.md` stays the single merge unit. The candidates are the sections first
  executed in the last fifth of a run:
  - Scout Digest;
  - Stale Items Auto-Archive;
  - Continuity-Dropoff;
  - Review Requests Never Drop;
  - compose-time count-guard;
  - Board Fields.

  Each candidate is deferred in its own PR, so a regression points at one block.

W4 is a pure text PR in `phases/`. It moves dated provenance paragraphs into
`phases/NOTES.md`, which is never globbed, and keeps a one-line *why* in place.

## 6. Non-goals

- Changing rule *content* in Phase A.
- Slicing DREAMING or RESEARCH, which are already mode-specific.
- Connector-aware assembly, which already exists.
- Editing `~/Scout` or `~/scout-plugin`, or migrating grafted vaults.
- Model choice, cadence and interactive-session usage. They are the bigger levers (§1, §9) and need their own issues.

## 7. Risks

| Risk | Mitigation |
|---|---|
| A sliced-out section is referenced by a kept one, for example a briefing row naming a consolidation-only section | Tier-0 test: for each mode class, every `**Bold Section Name**` cross-reference in the slice resolves inside the slice. Markers come only from `mode:` frontmatter, which already drives DREAMING/RESEARCH filtering. |
| Slicer bug drops rules | Fail-safe to the whole file. A tier-0 property test: slice(briefing) ∪ slice(consolidation) ⊇ every unmarked line. A golden test pins the per-mode slice of the shipped assembly. |
| The run edits the slice instead of `SKILL.md` | The slice lives under `.scout-cache/`, which is gitignored and overwritten every run, and the prompt names `SKILL.md` as the edit target. |
| Upgrade conflicts in customised vaults | Markers are boundary insertions only. A conflict goes to the existing sidecar flow and never blocks the upgrade. |
| Deferred pointer not followed (Phase B) | One deferral per PR. Eval checks cover those exact steps (`no_drop` for review requests, `demotion`), and the `brain token-turns` metric shows whether the read happened. |
| The saving is too small to matter | Stated up front (§1). Phase A is cheap. Phase B is optional. |

## 8. Measurement plan

### 8.1 Eval metric PR (lands first, no behaviour change)

Extend `tests/eval/harness/metrics.py` and `report.py METRICS` with:

- `cache read tokens`, already parsed but not reported;
- `cache creation tokens` (`cache_creation_input_tokens`, not parsed today);
- **`brain tokens loaded`**: chars/4 of `tool_result` content for Reads of
  `SKILL.md`, `DREAMING.md`, `RESEARCH.md` or `.scout-cache/brain/**`;
- **`brain token-turns`**: each such read's tokens × the assistant turns after
  it. This is the usage proxy, and it is the number W3 moves.

Each metric gets a tier-0 test on a committed synthetic stream.

### 8.2 Static budget (tier-0, CI)

A unit test assembles the shipped phases with all connectors enabled and pins
the per-mode slice sizes (`SKILL/briefing`, `SKILL/consolidation`, `DREAMING`,
`RESEARCH`) with 5 % headroom. Brain growth then becomes a visible, deliberate
pin bump instead of silent creep.

### 8.3 Model-backed evals

These run after **2026-10-15 14:00 Europe/Prague**, on Sonnet, on Jordan's own login.

```
plugin/scripts/eval.sh compare busy-weekday connector-down busy-briefing --base origin/main --reps 3
```

- `busy-weekday` and `connector-down` are both `morning-consolidation`.
- Phase A also changes the briefing view, so it adds **`busy-briefing`**: the
  `busy-weekday` vault and personas with `mode: morning-briefing`, and the
  `placement`, `no_drop` and `demotion` checks. The review-request audit is
  covered by `no_drop` (`ref:example-org/widgets#12`).
- Cost: 3 scenarios × 3 reps × 2 arms = 18 Sonnet sessions. The base arm is
  cached and reused across Phase B PRs.

**Acceptance per PR:**

- no check regresses: no `REGRESSION` line, and no check's head pass count below its base;
- `brain tokens loaded` (A) or `brain token-turns` (B) falls within ±10 % of the static prediction;
- `usage-equivalent $` and `turns` are not `WORSE`.

### 8.4 After rollout

One week of `session-tokens.jsonl` before and after the upgrade gives the
median cache-read per turn by mode. This only applies to vaults whose
`SKILL.md` carries markers. The maintainer's restructured vault falls back to
the whole file until it is marked.

## 9. Bigger levers to raise separately (not this spec)

1. `run-scout.sh.tmpl` hard-codes `--model opus` for every briefing and
   consolidation. A per-slot model setting, for example Sonnet for
   midday/afternoon consolidations, would likely beat all of #84.
2. Tool-result context growth: about 150k of the 194k average resident context
   is conversation and tool output, not instructions. Pre-session caching
   (`.scout-cache`) and terser connector queries reach that.
3. Interactive and agent-fleet sessions were about 90 % of the exhaustion week.

## 10. PR sequence

1. Eval metrics (§8.1) and the static budget test (§8.2). No behaviour change, tier-0 only.
2. W1 markers, the `brain slice` command, the runner wiring, backport stripping and the `busy-briefing` scenario. Includes an eval compare.
3. *(optional)* W3 deferrals, one PR per block. Each includes an eval compare.
4. *(optional)* W2 weekend class, plus a weekend eval scenario.
5. *(optional)* W4 history trim.

## 11. Phase A as built (2026-10-21)

Step 1 landed as #458. Step 2 (W1) follows §4, with these differences:

- **The close marker goes after the blank line that ends its section**, at the start of
  the next one, not straight after the section text. On the upgrade's own
  `three_way_merge`, the §4.1 layout conflicts when a vault appended a rule to the
  end of a marked section, the commonest vault edit. This layout merges that cleanly,
  and the rule stays inside its block. An insertion exactly where a marker goes
  (a heading edit, a new section next to a marked one) still meets a sidecar. Tests pin
  both cases.
- **One `phases/` change, frontmatter only.** The §7 cross-reference test found that the
  GitHub Inbound Scan (both modes) sends review requests to the three-bucket rule under
  *PRs Requesting …'s Review*, which sat in the briefing-only GitHub Query. That
  subsection is now its own section with `mode: [consolidation, briefing]`. Its text is
  unchanged, and the unmarked assembly is byte-identical to before.
- **`brain slice` prints the file the run reads** (the slice, or the live `SKILL.md`),
  rather than copying `SKILL.md` on fallback, and removes a stale slice whenever it
  writes none. The slice opens with a one-line "read-only view, edit SKILL.md" note.
- **Added:** `scoutctl brain check` (what each mode reads; exit 1 on damaged markers), a
  doctor warning for damaged markers, and a safety-rule test. The test checks that the
  privacy rule and its `check-outbound`/`check-query` gates, the write protocol, the
  live-engine rule and the review-request rules are in full in every run that applies
  them.
- `weekend-briefing` reads the briefing slice (W2 is Phase B).

Measured with every shipped connector enabled: SKILL.md ~42.5k tokens; briefing slice
~39.2k (−7.6%); consolidation slice ~39.4k (−7.2%, it keeps the review-request rule).
