# Eval harness, slice 4 (`compare`): implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** `plugin/scripts/eval.sh compare busy-weekday --base origin/main` scores the working tree
(head) against a base ref. It reuses cached base samples, and prints per-check verdicts that tell
flaky from regression.

**Architecture:** A `Checkout` is a plugin root plus the engine Python that installs from it:
- head is this checkout;
- base is a detached `git worktree` of the ref, with its own venv, cached by commit.

Each arm's vault is built by **that arm's** `scoutctl bootstrap install`, so its brain files,
runners and `scoutctl` are that arm's. Both arms are scored by the head's harness, so the scorer
is the same for both.

Base samples are cached under `~/.cache/scout-eval/baselines/<key>/`. The key is a content hash
of what shapes a run. Verdicts are a pure function over the two arms' samples.

**Tech stack:** Python stdlib, git, uv, pytest.

**Spec:** `docs/superpowers/specs/2026-10-12-eval-harness-design.md` §8 (compare and the cache) and
§9 (guardrails).

## Global constraints
Same as the slices 1–3 plan:
- no API key;
- CI tier 0 only, so no test starts a real `claude`;
- Sonnet default, `--opus` / `--many` opt-ins;
- N=1 Sonnet for live runs while building;
- ruff run from `plugin/engine`.

## Review focus
1. **A cache hit for a base whose phases differ.** The key must change whenever
   `plugin/phases/`, `plugin/templates/`, `plugin/engine/scout/`, the scenario, the harness
   version, the model alias or the CLI version changes.
2. **Arms that ran different resolved models or CLI versions.** `compare` must refuse a verdict.
3. **Any aborted sample in either arm.** There's no verdict, ever.
4. **A base ref that doesn't exist, or a dirty base worktree.** Fail before any session starts.
5. **N=1.** No regression verdict is possible, so the report says "need N≥2", not "no regression".

---

### Task 1: Sample persistence
- `run_sample` writes `meta.json` with: `scenario`, `rep`, `arm`, `model`, `resolved_model`,
  `cli_version`, `today`, `checkout`, `started_at`.
- `load_sample(result_dir) -> SampleResult` reads it back, together with `checks.json`,
  `metrics.json` and `aborted.json`.
- **Test:** a round trip with a fake-claude sample, and loading an aborted sample.

### Task 2: Checkouts
- `Checkout(root: Path, python: Path, label: str, commit: str | None)`.
- `head_checkout()` is this repo with its current venv.
- `base_checkout(ref, cache_root)`:
  1. resolves `ref` to a commit (an unknown ref raises `CheckoutError`);
  2. `git worktree add --detach <cache_root>/checkouts/<sha>` if absent;
  3. `uv venv` and `uv pip install -e plugin/engine` there if absent;
  4. returns it.
- `build_vault(..., checkout=...)` runs `install()` in-process for head (fast tests), and runs
  `<checkout.python> -m scout bootstrap install --no-jobs --skip-claude …` with `SCOUT_DATA_DIR`
  for other checkouts. The overlay, index, no-op cache script and git commit are unchanged.
- **Tests:**
  - the base checkout of `HEAD`, built into a tmp cache (marked `slow`), produces the same
    SKILL.md as head;
  - an unknown ref raises.

### Task 3: Cache key and store
- `content_hash(paths) -> str` is a sha256 over sorted (relative path, bytes).
- `cache_key(checkout, scenario, model, cli_version) -> str` hashes:
  - `plugin/phases`, `plugin/templates` and `plugin/engine/scout`, read from that checkout;
  - the scenario dir and the harness dir, read from head;
  - `HARNESS_VERSION`, the model and `cli_version`.
- `BaselineCache(root)` has `samples(key) -> list[SampleResult]` and
  `add(key, result_dir) -> Path` (it copies the result dir).
- **Tests:**
  - every input changes the key;
  - unrelated files don't;
  - add and read back.

### Task 4: Verdicts (pure)
`compare_arms(base, head) -> Comparison`. It has per-check rows
`(name, base k/n, head k/n, verdict)` and per-metric rows `(label, base median/min/max,
head median/min/max, worse: bool)`, plus a `blocked` reason. The rules are spec §8:
- **Stable vs flaky:** a check is stable at N/N and flaky at 0<k<N.
- **Regression:** base stable-pass and head ≤ N−2 passes. **Improvement:** the mirror.
- **Flaky base:** the row reads `noisy` and is excluded from the verdict.
- **Not enough samples:** `need N≥2` when either arm has fewer than 2 scored samples.
- **Metrics:** worse only when the head median is above the base max.
- **Blocked:** `aborted` or `mixed models` / `mixed CLI` when the arms differ.

**Tests:** each rule, including N=1, a noisy base, an abort, and mixed models.

### Task 5: `eval.sh compare`
`compare <scenarios…> --base REF [--reps N] [--fresh-base] [--opus] [--many] [--yes] [--allow-weekend]`.
- The plan counts head reps plus base cache misses, and prints the cache state.
- Fresh base and head samples are interleaved. Cached base samples are reused and their age is
  shown, with a flag past 14 days.
- New base samples are added to the cache.
- The report prints the comparison.
- **Exit codes:** 0, or 1 when any row is a regression, or 2 on usage errors.
- **Tests (fake claude, with base = `HEAD`):** plan counts, the cache hit on a second run, and
  `--fresh-base` ignoring the cache.

### Task 6: One live compare
`compare busy-weekday --base origin/main --reps 1`: two Sonnet sessions on a cache miss. Then run
it again to see the cache hit, with one session. Record what happened in the spec §8, then review,
open the PR and send the number.
