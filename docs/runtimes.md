# Agent runtimes: the evidence table

Which agent CLIs run Scout's scheduled runs, and what was checked for each.
This file is the record that spec
[`2026-11-13-runtime-agnostic-design.md`](./superpowers/specs/2026-11-13-runtime-agnostic-design.md)
§13 asks for. Any claim about a runtime, in the README, on the site or in the
app, must point at its row here.

One row per runtime. A cell holds a result and a date, a version, or "not yet".

| Runtime | CLI version tested | Tier | Gate probe | Conformance suite | `kb-gate` | Privacy scenario | Needs | Shipped in |
|---|---|---|---|---|---|---|---|---|
| Claude Code (`claude-code`) | 2.1.318 | F | passed, 2026-11-16 | not yet built | not yet run through the new path | not yet | paid Claude plan | v0.18.15 |

## The columns

- **CLI version tested:** the version the gate probe last passed on.
- **Tier:** spec §8.3. F and L may run on the vault. S exists on paper only.
  A runtime at Tier D may not read the vault.
- **Gate probe:** `scoutctl gates probe` (spec §3.5). It runs the CLI once,
  with the runners' flags, against a local fake model in a throwaway vault,
  and passes when Scout's `kb-read-gate` hook blocks the probe's read. It
  costs nothing and uses no credentials. The runners run it before each
  scheduled run and skip the run when it fails.
- **Conformance suite:** spec §11.2, built in Phase 2 with the first
  non-Claude adapter.
- **`kb-gate`:** `plugin/scripts/eval.sh kb-gate` (the KB-writing release
  gate), run on this runtime through `scoutctl agent-runtime exec`. Phase 1
  task 1.9 records Claude Code's date here.
- **Privacy scenario:** spec §11.3's eval scenario, run on this runtime.
- **Needs:** the subscription or account a scheduled run uses.
- **Shipped in:** the first Scout release whose runners launch through this
  runtime's adapter.

## How a row is updated

- **Gate probe and CLI version:** run `scoutctl gates probe --json` against
  a throwaway vault (`--vault <empty dir>`), never your real one. Copy
  `claude_version` and the date of `checked_at`.
- **`kb-gate` and privacy scenario:** these start a model session on the
  maintainer's own login. Run them only with the maintainer's OK, and record
  the date and pass or fail.
- A row changes in the same PR as the evidence it records.
