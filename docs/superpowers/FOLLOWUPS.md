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

### tests/integration/test_action_items_watch.py — silent-crash failure mode (Plan 3)

- **(minor)** `_read_until` doesn't check `proc.poll()` and stderr is
  captured to a `PIPE` that's never surfaced on assertion failure. If
  `scoutctl action-items watch` exits immediately (import error,
  CLI-arg drift, missing watchdog), the test waits the full 10s and
  then fails with `assert "completed" in ""` — uninformative.
  Cleanup: short-circuit the read loop on `proc.poll() is not None`
  and include `proc.stderr.read()` in the assertion message so a
  broken CLI fails fast and points at the real cause. Not blocking;
  the test is reliable when the CLI is healthy. Surfaced by code
  review on commit `5624cbc`.

### scout.action_items.watch (Plan 3 — landed in plan-3 branch)

- **(minor)** `_parse_text` writes a tempfile and calls
  `parser.parse_file(Path)` rather than parsing a string directly. Two
  tempfile cycles per diff. Invisible at personal scale, but if the
  parser ever grows a `parse_text(str)` overload, `_parse_text` becomes
  a one-liner pass-through and the helper can disappear. Don't preempt
  — wait for the parser to evolve.
- **(minor)** The `# type: ignore[override]` on `_Handler.on_modified`
  in `watch.py` is opaque. A single inline word — "watchdog stubs
  widen `event` to `FileSystemEvent`" — would orient the next reader
  without growing the comment.

### scout.action_items.render.render_changes (Plan 3 — landed in plan-3 branch)

- **(minor)** In color mode, `render_changes` constructs a fresh
  `rich.console.Console` + `StringIO` per event. Sub-millisecond at
  personal scale (1–50 events per file change), so not a real
  bottleneck. If Task 3's watcher ever surfaces large batched diffs
  in profiling, hoist the `Console` outside the loop or switch to
  `console.capture()`.

### scout.action_items.diff (Plan 3 — landed in plan-3 branch)

- **(important)** `_compare`'s status-transition logic collapses
  `ActionItem.status` (which ranges over `{open, done, in_progress,
  watching}` per `parser.py`) into a binary `done`/not-`done` view:
  `kind = "completed" if curr.status == "done" else "reopened"`. This
  means transitions like `in_progress → watching` or `open → watching`
  also emit `reopened`, which can produce misleading watch output for
  pure section reshuffles. The plan's tests cover only `open ↔ done`,
  so the bug doesn't surface in unit testing. Two reasonable fixes
  before the v0.5 event store consumes these:
  - Suppress emit unless one side is `done` (`open → in_progress`
    silent), or
  - Introduce a `status_changed` event with `extras={"old", "new"}`
    for non-`done` shuffles. Note this expands the renderer's
    vocabulary (Plan 3 Task 2) by one kind.

  Surfaced by code review on commit `c6b3d77`. Decide before v0.5's
  event-store substitution forces the audit anyway.
- **(minor)** `ChangeEvent.item_id: str` uses `""` as a sentinel for
  unprefixed lines. `str | None` would model absence more honestly
  but ripples through every consumer (renderer, future event-store
  serializer). Worth revisiting when v0.5 forces a wider event-shape
  audit.

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

## Plan 5 → Plan 6 carryforward — Schedules tab rewrite (important)

### Background

Plan 5 collapsed the per-slot launchd plist model
(`com.scout.briefing.plist`, `com.scout.consolidation-7pm.plist`, etc. —
8 separate plists, one per scheduled fire) into a single
`com.scout.schedule-tick.plist` that runs the `scoutctl schedule tick`
dispatcher every 5 minutes. The dispatcher reads slots from
`~/Scout/.scout-state/schedule.yaml` and decides what to fire.

Side-effect: the existing **Schedules** tab in scout-app — built around
`ScheduleEditorService` reading/writing `~/Scout/launchd/com.scout.*.plist`
files via `PlistIO` and a `FileWatcher` — is no longer aligned with how
Scout schedules work. The data model it edits (label/runner/trigger per
plist) has shrunk to two rows (heartbeat + schedule-tick), neither of
which the user should be editing in this UI. Worse, the burst plist
deletions during Plan 5 deployment caused the `FileWatcher` to thrash
`loadAll` → `@Published` → SwiftUI render storm, freezing the app on
sidebar transitions.

### What Plan 5 did (interim)

1. Hid `.schedules` from the sidebar in `Scout/Shell/SidebarView.swift`
   (case kept in `SidebarItem` for state-restore compat).
2. Replaced `SchedulesView.body` with a `ContentUnavailableView`
   placeholder pointing at this followup. Existing implementation moved
   to `legacyBody` + helpers (kept intact for Plan 6 reuse).
3. Stopped calling `editor.loadAll()` and `editor.startWatching()` in
   `AppState` so the FileWatcher doesn't run at all (avoids the render
   storm on plist churn). `ScheduleEditorService` is still constructed
   but unused.

### What Plan 6 must do

- **Rewrite `SchedulesView` against `schedule.yaml`.** Read slots via
  `scoutctl schedule list` / `show <key>` (machine-parseable JSON
  already exists). The tab should list each slot's
  key/type/runner/fires_at_local/weekdays/on_miss/cooldown_minutes —
  the same shape that `engine/scout/schedule.snapshot.json` exposes.
- **Decide how editing works.** Two options:
  - **Overlay file** at `~/Scout/.scout-state/schedule.local.yaml`. The
    loader already supports an overlay (see `scout.schedule.load_schedule`
    `overlay` parameter). The editor writes a per-slot diff into the
    overlay; `scoutctl schedule reload` picks it up. Non-destructive —
    the canonical `schedule.yaml` is untouched, easy to revert.
  - **Direct edit** of `~/Scout/.scout-state/schedule.yaml`. Simpler, but
    drops the "user customization on top of plugin defaults" pattern.
  Recommend the overlay path; it's already wired in the loader.
- **Validation feedback.** Reuse `scoutctl schedule validate` (already
  exists) for save-time correctness checking. Surface errors inline.
- **Run-now from the editor.** Each row should have a "Fire now" button
  that calls `AppState.fireNow(slotKey:bypassBudget:)` (the same helper
  the Control Center upcoming strip uses). `RunDetailView` and
  `MenuBarExtraContent` already use this pattern.
- **Drop `ScheduleEditorService`** and its `Plist`/`Schedule` types
  from `Scout/Models`/`Scout/Services` once the rewrite ships. Also
  drop `Scout/Schedules/{ScheduleDetailView,NewScheduleSheet}.swift`
  and the helper functions in `legacyBody` of `SchedulesView`. The
  `SidebarItem.schedules` case should re-enter the visible sidebar
  in `SidebarView`.
- **Lift `ScheduleService`'s ProcessRunner pattern.** The new editor
  service should also accept `runner: any ProcessRunner` + `scoutctl: URL`
  via init (mirroring `ScheduleService`) so it's testable without
  shelling out for real.

### What NOT to drop yet

`ScheduleDiff.swift`, `PlistIO.swift`, `ScheduleTriggerFormatter.swift`,
`SystemLaunchctlClient.swift` — these still serve `com.scout.heartbeat`
and `com.scout.schedule-tick` editing/inspection. The Plan 4-supplement
heartbeat redesign may want them. Audit during Plan 6, but don't
preemptively delete.

### Test coverage to add in Plan 6

Today's `ScheduleEditorServiceTests` (Plist round-trip + drift detection)
will need to be replaced by tests against the new YAML editor. The
`PlistIO`/`ScheduleDiff` test files can stay if those types survive (see
above). Build a `MockProcessRunner` queue stub that returns canned
JSON for `scoutctl schedule list` / `show` / `validate` — the pattern
landed in `ScoutTests/Services/ScheduleServiceTests.swift` is the
template.

## Plan 7 followup — Schedules table responsive resizing (minor)

Plan 7 shipped a clean DS-aligned Schedules tab with master/detail layout
(`NavigationSplitView`), but the Table view's 6 fixed-width columns
(NAME flexible · TYPE 140 · TIME 70 · DAYS 250 · ON MISS 90 · COOLDOWN 90,
+ 5×16pt spacing + 32pt horizontal padding ≈ 752pt + NAME) overflow
when the user drags the master/detail divider to make the master pane
narrow. The current commit puts a `.lineLimit(1)` + `.fixedSize` on the
NAME slot-key text so rows stay tight rather than wrapping into
multi-line giants — but the visual is rough at narrow widths (columns
clip past the right edge; text bleeds out).

Acceptable today (it only manifests when the user actively drags the
split). Three options for a future polish plan:

1. **Hide low-priority columns below a width threshold.** ON MISS and
   COOLDOWN drop out first; DAYS' inline label disappears next; NAME
   stays. Use `GeometryReader` or `@Environment(\.horizontalSizeClass)`
   semantics to decide.
2. **Switch to a horizontally-scrollable inner ScrollView.** Keeps all
   columns visible but adds a horizontal scroll affordance.
3. **Auto-fall-back to Cards view at narrow widths.** Cards are already
   adaptive (`LazyVGrid(.adaptive(min: 240))`) and degrade to single
   column gracefully. Switch view mode programmatically when the
   master pane width drops below ~720pt.

Recommendation: **option 1**. Cleanest for power users who'll regularly
adjust the split; preserves the at-a-glance density Jordan asked for.
Implementation is contained: `SchedulesMasterTable` reads the geometry
of its own frame and conditionally hides cells.

Not blocking the Plan 7 merge. File its own scoped plan when this
becomes annoying enough to warrant a session.
