# Followup Items

Tracking items surfaced during code reviews that aren't blocking
individual PRs but should be addressed in subsequent work. Use this as
the single catalog — don't let items decay in PR comments.

Items here span all three Scout repos:
- `scout-plugin` (this is where most engine followups live today)
- `scout-app` (this repo; Plan 6 will land followups here)
- User data dir (`~/Scout`; no code followups, just migration ordering)

## How to use this file

- Items are tagged by priority:
  - **blocker** — must address before the enclosing PR merges
  - **important** — should address soon; file as its own PR or bundle with related work
  - **minor** — polish; pick up when touching the relevant code
- When opening a PR that addresses an item, reference it by link / anchor
  and move the item to the `Resolved` section at the bottom with the PR number.
- New items discovered in review should be added here, not lost in PR comments.

---

## Open

### Cross-cutting (affects multiple modules in scout-plugin/engine/scout/)

_All three "important" cross-cutting items addressed in scout-plugin
PR #6 (polish/plan-1-followups). See the Resolved section below._

The partial fix in PR #6 covers `scout.config` (wheel-ready via
`importlib.resources`) but leaves `scout.manifest.ENGINE_DIR` still
using `Path(__file__).parent.parent`. That path is only consulted by
`scoutctl manifest build`, which is a dev-only operation that targets
the editable engine clone — under wheel install there is no
"engine dir" to write into. Revisit when a non-editable use case
appears (e.g., a packaged `.app` bundling the engine).

### scout.errors

- **(minor)** `SchemaVersionMismatch` message references
  `scoutctl migrate data-dir --from X --to Y`, but the `migrate` command
  doesn't exist until Plan 4. Acceptable as a forward-looking message;
  flag when Plan 4 reviews happen.
- **(minor)** `test_errors.py` doesn't assert `err.have == 1` /
  `err.want == 2` on `SchemaVersionMismatch` — the attribute contract is
  untested even though call sites will read it.
- **(minor)** No explicit test that `ScoutError` is raisable and caught
  as a base `Exception` — a future refactor of the base class could
  silently break this.

### scout.paths

- **(minor)** Empty-string `SCOUT_DATA_DIR` (from a misconfigured shell)
  silently falls back to `~/Scout` via `if env:` truthy check. Behavior
  is reasonable but unpinned. Add a test asserting this, so a future
  "fix" of the truthy check can't silently change semantics.
- **(minor)** `data or data_dir()` in every derived helper treats
  `Path("")` as None (`bool(Path("")) is False`). Type hint
  `Path | None` slightly lies. Fix: change to
  `data if data is not None else data_dir()`.
- **(minor)** No test that `data_dir()`'s explicit-argument branch
  expands tildes. `test_data_dir_expands_tilde` covers only the env-var
  branch. Cheap insurance against a future refactor.
- **(minor)** `require_data_dir` doesn't distinguish broken symlinks,
  permission errors, or "exists but inaccessible". Acceptable for v1;
  revisit if real users hit these.

### scout.config

- **(minor)** No test covers `load_config(data_dir=None)` — the
  `paths.config_path()` → `paths.data_dir()` resolution chain. All
  current tests pass `fake_data_dir` explicitly.
- **(minor)** `_env_overrides` whitelist is two `if v := …` branches.
  Refactor to a declarative
  `_ENV_MAP = [("SCOUT_USER_EMAIL", ("user", "email")), …]` when N≥4
  env vars.
- **(minor)** `_deep_merge` replaces lists rather than concatenating.
  This is correct config-library behavior (Hydra, Dynaconf default).
  Document in the docstring when lists first appear in the schema, so
  callers aren't surprised.

### scout.manifest

- **(important)** Circular-import risk: `from scout import __version__`
  works today because `scout/__init__.py` is minimal, but adding
  transitive imports to `__init__.py` (e.g., a
  `from scout.manifest import EngineManifest` convenience re-export)
  would cause a loop. Options: (a) keep `__init__.py` bare,
  (b) read the version via `importlib.metadata.version("scout-engine")`,
  (c) move `__version__` to a leaf `scout/_version.py`.
- **(minor)** `write_manifest(path)` always calls `build_manifest()`
  internally — can't write a custom/test manifest to disk. Add an
  optional `manifest: EngineManifest | None = None` kwarg if a second
  caller emerges.
- **(minor)** `test_manifest.py` doesn't assert `sort_keys=True`
  stability — test name says "stable and decodable" but only checks
  decodability.
- **(minor)** No test asserts the trailing newline on file write.
- **(minor)** No test asserts all features are False at v0.4.0 (the
  scaffolding invariant). Should fail loudly when Plan 2 flips
  `action_items_cli_v1` without updating the test.

### scout.cli

- **(important)** ~~No `test_cli.py`.~~ Resolved in scout-plugin PR #6
  — added `CliRunner`-based tests for exit-code forwarding, `manifest
  build` file write, `manifest show` JSON output, `version` equality
  with `__version__`, and end-to-end subprocess via the wheel smoke
  test.
- **(minor)** `print()` used four times. Swap to `typer.echo()` for
  broken-pipe handling (`scoutctl version | head` without a `BrokenPipeError`)
  and future `err=True` / color options.
- **(minor)** `manifest build` success message is human-only. Future
  `--json` mode should emit `{"path": "…"}`.

### engine/bin/scoutctl (launcher shim)

- **(minor)** `ENGINE_DIR="${DIR%/bin}"` is a conditional suffix strip.
  If the shim is symlinked to a location whose parent isn't named `bin`
  (e.g., `~/.local/bin/`), `ENGINE_DIR` doesn't strip correctly and the
  venv lookup fails — degrades to `python3 -m scout.cli` fallback.
  Consider `readlink -f` canonicalization before `dirname`, or document
  that symlink installs hit the fallback Python.
- **(minor)** Fallback `python3 -m scout.cli` failure emits a raw
  `ModuleNotFoundError`. A pre-check with a friendlier error message
  ("scout package not installed — run `pip install -e engine/[dev]`")
  would help LaunchAgent debugging.

### tests/perf (import discipline + latency)

- **(important)** AST check in `test_no_heavy_imports.py` only scans
  `scout/cli.py` directly. Transitive imports — `cli.py` imports a
  lightweight module X, which imports `textual` — pass silently. Use
  `python -X importtime -c 'import scout.cli'` and parse the trace to
  assert no banned module appears, or walk the import closure.
- **(important)** `BANNED_TOP_LEVEL` is missing HTTP libraries
  (`requests`, `httpx`, `aiohttp`) and heavy data libraries (`pandas`,
  `numpy`). Scout will grow HTTP (connector health, usage API). Add
  preemptively so the first "lift HTTP to the top" regression fires
  the test.
- **(minor)** `test_startup.py` has no warm-up run. First subprocess
  pays filesystem cache-miss cost. Add a throwaway invocation before
  `time.perf_counter()` to stabilize the measurement, especially on
  cold CI runners.

### CI (.github/workflows/)

- **(minor)** `astral-sh/setup-uv@v3`'s caching is disabled. Add
  `enable-cache: true` + `cache-dependency-glob: engine/pyproject.toml`.
  Saves ~3–5s per job across the 4-row matrix.
- **(minor)** No concurrency groups. Rapid-fire pushes queue redundant
  runs. Add:
  ```yaml
  concurrency:
    group: ${{ github.workflow }}-${{ github.ref }}
    cancel-in-progress: true
  ```
- **(minor)** Perf tests run alongside unit tests. If CI runners get
  flaky, split perf into its own job (or use `-m "not perf"` on main
  plus a soft-fail perf job).
- **(minor)** `pytest-cov` is in `[dev]` but CI doesn't collect
  coverage. Add `pytest --cov=scout --cov-report=xml` once there's a
  reason to track it (e.g., PR coverage comments).
- **(minor)** `mypy scout` but not `mypy tests`. Relaxed-config mypy
  for tests catches fixture-signature drift. Re-evaluate once the
  engine stabilizes.
- **(minor)** Node.js 20 deprecation warning from `actions/checkout@v4`
  and `astral-sh/setup-uv@v3`. Auto-enforced to Node 24 in June 2026.
  No action needed now; upgrade when newer major versions of those
  actions ship.
- **(minor)** `apt-get install -y shellcheck` runs every lint job.
  Low ROI to cache; leave as-is.

### pyproject.toml

- **(minor)** `[tool.mypy]` has `strict = false`. Revisit when the CLI
  surface grows. Flip to strict on a per-module basis —
  `scout.manifest`, `scout.config`, `scout.paths` are pure enough today.
- **(minor)** `concurrency` pytest marker is declared but has no
  consumers. Will be used by Plan 2+ concurrency tests per the spec
  (§ 6 Concurrency and file-locking rules).

### scout.ids (Plan 2/3 — not yet implemented; v0.4 spec §13.1)

- **(minor)** `id-map.json` retention policy is unspecified. When a user
  deletes a task in Obsidian, its `[#A3F7]→ULID` mapping becomes orphaned.
  Decide during implementation: retain forever (text is cheap, audit
  value), or prune entries whose `last_seen_in_file` is older than N
  days. Default to "retain forever" — the file is bounded by the user's
  total lifetime task count, which won't reach concerning sizes for
  years. Worth a load-test once `id-map.json` exceeds ~10MB.

### scout.action_items.writer (Plan 2/3 — not yet implemented; v0.4 spec §6, §13.1)

- **(minor)** Obsidian "Safe Save" / iCloud-sync interaction with our
  atomic-rename writes is unverified. Spec relies on `os.replace(tmp,
  final)` being POSIX-atomic, which Obsidian generally handles cleanly,
  but in some configurations (iCloud syncing the vault, or specific
  timing) external overwrites trigger Obsidian's "External changes
  merged" notification. When implementing `writer.py` tests, include a
  manual verification step: open today's action-items file in Obsidian,
  trigger a `mark_done` from CLI, confirm no popup appears. If popup
  reproduces, evaluate alternatives (file-handle hold + truncate-write
  vs. rename) before locking in atomic-rename as the v0.4 invariant.

### v0.5+ event store (Plan 6+ implementation; vision spec §"Egress failure handling")

- **(minor)** `scoutctl connector dead-letter retry <event_id>` re-emit
  semantics: implement as *new event with copied payload + new ULID +
  current timestamp + reference to the failed original*, NOT as
  re-insertion of the original row. Strict event-sourcing invariant:
  the log's `ts` ordering must remain monotonic; you never inject past
  events into the present. The retry event's payload includes
  `replay_of: <original_event_id>` for audit. The tombstone on the
  failed original stays in place.

---

## Resolved

_(Move entries here as PRs close them. Format:
`- **[item title]** — PR #N, date.`)_

### scout-plugin PR #6 — polish: plan-1 followups (2026-04-24)

- **Wheel-packaging readiness (cross-cutting, important)** — moved
  `engine/defaults/` under `engine/scout/defaults/`; `scout.config`
  now resolves the file via `importlib.resources.files() + as_file()`.
  Added slow-marked smoke test (`tests/smoke/test_wheel_install.py`)
  that builds a wheel, installs it in a fresh uv venv, and verifies
  `scoutctl version`, `manifest show`, and `load_config()` all work.
  Note: `scout.manifest.ENGINE_DIR` left as-is — only used by
  `scoutctl manifest build` which is a dev-only operation.
- **Unexpected-exception policy in `scout.cli.main` (cross-cutting,
  important)** — `main()` now catches `Exception` and maps to
  reserved exit code `70` with `scoutctl: internal error: <Type>:
  <msg>` to stderr. `KeyboardInterrupt` and `SystemExit` propagate
  unchanged. Tests cover all four paths.
- **Subcommand drift between `scout.manifest` and `scout.cli`
  (cross-cutting, important)** — `build_manifest()` now derives
  `subcommands` by walking the click group built from
  `scout.cli.app`, so adding a `@app.command()` automatically updates
  the manifest. Lazy imports of `typer.main` and `scout.cli` keep
  `scoutctl` startup unchanged. Test monkeypatches a dummy
  `CommandInfo` onto the app and asserts it appears in the manifest.
- **`scout.cli` — No `test_cli.py` (important)** — added full
  `CliRunner` coverage of `version`, `manifest show`, `manifest
  build`, no-args help, and `main()` error dispatch (clean return,
  `ScoutError` forwarding, unexpected-exception mapping,
  `KeyboardInterrupt` and `SystemExit` propagation).
