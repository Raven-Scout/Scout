# Custom Connectors — App Contract & Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the app-contract and engine-hardening items deferred from #261 (tracked in Raven-Scout/Scout#321), so the Mac app can drive custom connectors safely and the engine handles hand-edited vault files gracefully.

**Architecture:** Bounded follow-up fixes on the merged custom-connectors engine. No new subsystem. Every change lives in the modules #261 added or touched: `scout/custom_connectors.py`, `scout/scripts/custom_connector_ops.py`, `scout/scripts/custom_assembly.py`, `scout/scripts/bootstrap.py`, `scout/connectors.py`, `scout/scripts/connector_probes.py`, `scout/hooks/connector_log.py` and `scout/cli.py`.

**Tech Stack:** Python 3.11+, Typer, PyYAML, pytest; engine at `plugin/engine/` in the Raven-Scout/Scout monorepo.

**Spec:** `docs/superpowers/specs/2026-10-02-custom-connectors-design.md` (binding), plus the deferred items listed in Raven-Scout/Scout#321. Design approved by Jordan in chat on 2026-10-06 ("yes, go ahead and merge when green").

## Global Constraints

- **JSON contract:** `scoutctl connectors custom …` always prints exactly one JSON object. Exit codes: `0` applied/unchanged/valid/dry-run, `1` error, `2` invalid/probe-failed, `3` deferred/conflict, and NEW `4` busy (the vault session lock is held).
- **Snapshot:** custom rows never enter `connectors.snapshot.json`, and no `*.snapshot.json` is ever hand-edited.
- **Never crash on hand edits:** a malformed `connectors.custom.yaml` or `scout-config.yaml` must never crash assembly, the roster, the probe registry, the hook, or the health report.
- **Fixtures are anonymized** (repo `CLAUDE.md`): generic server names (`example_suite`, `dataplat`), people `Alex`/`Priya`/`Sam`, no real vendors.
- **Lint gates,** run from `plugin/engine`: `.venv/bin/ruff check scout tests`, `.venv/bin/ruff format --check scout tests`, `.venv/bin/mypy scout`. Line length 120.
- **Commits** run from the repo root with repo-relative paths. Each message ends with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Paths in `plugin/CHANGELOG.md`** are plugin-relative (`engine/...`).
- **Live data:** never touch `~/Scout` or `~/scout-plugin`. Tests use tmp dirs (`tmp_path`, `fake_data_dir`); HOME is already hermetic.

## Review Focus

1. **Comment-preserving writes must never lose data.** If the per-entry rewrite cannot reproduce the target mapping exactly, it must fall back to a plain dump. Pinned in Task 6.
2. **`--no-wait` and the busy timeout must still release nothing they don't own,** and must print JSON. Pinned in Task 1.
3. **Filtering health/probes by `connectors.enabled` must not hide shipped connectors,** and must fail closed (no custom rows/probes) when `scout-config.yaml` is unreadable. Pinned in Task 3.
4. **Wrapper skipping must not mislabel shipped `gh` calls,** and `timeout 10 gh pr list` should now label `github`. Pinned in Task 4.
5. **Sanitizing must not reject valid multi-line `notes`** that merely contain a `#` mid-line (e.g. "see PROJ-1234 #2"). Pinned in Task 5.

## Before you start

Repo: `~/.scout-worktrees/scout-custom-connectors` (a clone of Raven-Scout/Scout), branch `feat/custom-connectors-hardening` (off main `e8ff5b4`). The venv exists at `plugin/engine/.venv`. Baseline: `cd plugin/engine && .venv/bin/pytest tests/unit -q -p no:cacheprovider`.

---

### Task 1: `busy` status and `--no-wait` for add/remove

**Files:** `plugin/engine/scout/scripts/custom_connector_ops.py`, `plugin/engine/scout/cli.py` (`connectors custom add` / `remove`), tests in `plugin/engine/tests/unit/test_custom_connector_ops.py` and `plugin/engine/tests/unit/test_cli_connectors_custom.py`.

**Produces:** `Outcome` status `"busy"` with exit code `4`. `ops.add(..., wait: bool = True)` and `ops.remove(..., wait: bool = True)`. CLI flag `--no-wait` on both.

- `_under_lock` currently calls `acquire_lock_with_wait(lock)` and maps `LockBusyError` to `Outcome("error", …)`. Change it to:
  - take a `wait: bool` argument;
  - when `wait` is False, call `acquire_lock_with_wait(lock, timeout_s=0)`, which tries once;
  - map `LockBusyError` (from either path) to `Outcome("busy", key, message="A Scout session is running; try again when it finishes.")`.
- Add `"busy": 4` to the exit-code table.
- Thread `wait` from `add`/`remove` through to `_under_lock`. `--dry-run` never takes the lock (unchanged).
- CLI: `--no-wait` (bool, default False) on `custom add` and `custom remove`, passed as `wait=not no_wait`.
- **Tests (TDD).** Simulate a held lock by monkeypatching `custom_connector_ops.acquire_lock_with_wait` with a fake that records its `timeout_s` and raises `LockBusyError(lock, 12345)`. A real lock file holding this test's own PID would count as already held by us, so don't use one. Assert:
  - `add(..., wait=False)` gives status `busy`, exit 4, the fake was called with `timeout_s=0`, and nothing was written (custom file and config unchanged);
  - `add(..., wait=True)` with the same fake also gives `busy`, exit 4, and the fake was called with the default timeout;
  - `remove` behaves the same both ways;
  - the CLI with `--no-wait` prints JSON with `"status": "busy"` and exits 4.

### Task 2: `custom list` returns full definitions and invalid rows

**Files:** `custom_connector_ops.py` (`list_custom`), tests in `test_custom_connector_ops.py`.

**Produces:**
- Every valid row gains `"valid": true` and `"definition": <the entry mapping exactly as in the file>`.
- Invalid entries (present in `CustomLoad.raw` but not in `.connectors`) become rows:
  `{"key", "valid": false, "enabled", "display_name": <str or null>, "definition": <raw value>, "issues": [{"path","message"}...]}`.
  - The issues are those whose path starts with `connectors.<key>`.
  - Rows are sorted by key across valid and invalid.
- The top-level `"issues"` list still carries every issue, including file-level ones.
- **Tests:**
  - A valid row's `definition` equals the YAML entry.
  - A broken entry appears as an invalid row with its issues.
  - A file-level problem (unparseable YAML) yields no rows and one top-level issue.

### Task 3: Health roster and probes follow `connectors.enabled`

**Files:** `plugin/engine/scout/custom_connectors.py` (new helper), `plugin/engine/scout/connectors.py` (`_custom_roster_entries`), `plugin/engine/scout/scripts/connector_probes.py` (`_custom_probes`), tests in `plugin/engine/tests/unit/test_custom_connectors_health.py`.

**Produces:** `cc.enabled_keys(vault: Path) -> set[str]`.
- Reads `<vault>/scout-config.yaml` tolerantly and returns `set(connectors.enabled)` of strings.
- Returns an empty set when the file is missing, unreadable, malformed, or the wrong shape. Fail closed: no custom row or probe for a connector we can't confirm is enabled.
- Never raises.

Both derivations keep only custom connectors whose key is in `enabled_keys(data_dir)`. Shipped and overlay rows and probes are untouched.

`connectors detect` already reports a bash probe's command in `evidence` (`` `cmd` exit N ``), so no change is needed there; add a test asserting the command appears in the detection evidence for a custom bash probe.

**Tests:**
- An enabled custom connector appears in the roster and probes; a disabled one does not.
- An unreadable or missing `scout-config.yaml` gives no custom rows or probes, while shipped rows remain.
- Detection evidence includes the probe command.

### Task 4: Hook sees through wrapper commands; generic list; honest remediation

**Files:** `plugin/engine/scout/custom_connectors.py` (`first_binary`, `_GENERIC_BINARIES`, new `is_generic_binary`), `plugin/engine/scout/hooks/connector_log.py` (`_bash_key`), `plugin/engine/scout/connectors.py` (remediation), tests in `plugin/engine/tests/unit/test_custom_connectors_model.py`, `plugin/engine/tests/unit/test_hooks_connector_log.py` and `test_custom_connectors_health.py`.

**`first_binary(cmd)` returns the first real command word.** It walks the tokens and skips:
- `FOO=bar` assignments;
- wrapper commands in `_WRAPPERS = {"env", "sudo", "nice", "nohup", "exec", "command", "time", "timeout", "xargs", "caffeinate"}`;
- option tokens starting with `-`;
- the duration argument right after `timeout` (`^\d+(\.\d+)?[smhd]?$`);
- the numeric argument right after `nice -n`.

The result is still a basename. Examples:
- `timeout 10 tixcli list` → `tixcli`
- `env FOO=1 nice -n 5 tix list` → `tix`
- `sudo -u alex tix x`: `-u` is skipped, then `alex` would be read. Accept that: document it as a known limit in the docstring.

Add to `_GENERIC_BINARIES`: `osascript`, `npm`, `docker`, `ssh`, `open`, `echo`, `cat`, `printf`, `sh`, `bash`, `zsh`.

**`_bash_key`** uses `custom_connectors.first_binary` on each segment (instead of its own env-skip loop), so wrappers are seen through for shipped `gh` too. Keep its segment splitting and the `bash:<first-token>` fallback. The fallback label stays the raw first token, so labels for unrelated commands don't change.

**`is_generic_binary(name) -> bool`** is exposed. In `connectors._custom_roster_entries`, when a bash-keyed connector's probe binary is generic or None, `first_fix` becomes `f"Check that {names}'s command works in a terminal, then re-add it with `scoutctl connectors custom add --file <definition>`."` (at most 180 characters).

**Tests:**
- the `first_binary` cases above;
- the hook labels `timeout 10 tix list` as `tickets` and `timeout 5 gh pr list` as `github`;
- `bash:curl` is unchanged;
- two `osascript` connectors don't claim each other's calls (`bash_binaries` has no `osascript` entry);
- remediation for a curl-probed connector names the connector.

### Task 5: Sanitize free text and undeclared inputs

**Files:** `plugin/engine/scout/custom_connectors.py` (`parse_connector`), tests in `test_custom_connectors_model.py`.

**Rules:**
- **`display_name`** must be a single line with no `\n`/`\r`. Issue on violation: "must be a single line".
- **`notes`** may span lines, but no line (after `lstrip`) may start with `#` followed by a space or another `#`, i.e. no markdown heading. Issue: "must not contain markdown headings; they would break the brain file's structure". A `#` mid-line is fine.
- **The same heading rule applies to `focus`/`when` guidance text,** for explicit text only; preset text is shipped and trusted.
- **Every `{{INPUT_<NAME>}}` placeholder** found anywhere in the entry's strings must correspond to a declared `needs_user_input` name (`NAME.lower()`). Each undeclared one gets an Issue at `connectors.<key>.needs_user_input`: "uses {{INPUT_X}} but does not declare `x` in needs_user_input".

**Tests:**
- each violation;
- `notes: "see PROJ-1234 #2"` is accepted, and so is multi-line notes without headings;
- a declared input is accepted.

### Task 6: Comment-preserving `custom_connectors.write`

**Files:** `plugin/engine/scout/custom_connectors.py` (`write`, `dump`), tests in `test_custom_connectors_model.py` and `test_custom_connector_ops.py`.

**`write(vault, raw_connectors)`.** When the existing file parses to `{"schema_version": 1, "connectors": {...}}` and is laid out block-style, with `connectors:` at column 0 and each key at exactly two spaces of indent, rebuild the text entry by entry:
- **Keep verbatim:** the header and comment lines before `connectors:`, every unchanged entry's lines (including comments inside or directly above it), and any trailing comments.
- **Replace** a changed entry with `yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True)` indented by two spaces. Keep the comment lines directly above it.
- **Drop** a removed entry together with the comment lines directly above it.
- **Append** new entries at the end of the block.
- **Change detection:** an entry is unchanged when `yaml.safe_load` of its original segment equals the new value.
- **Safety net:** after building, `yaml.safe_load(result)` must equal `{"schema_version": 1, "connectors": raw_connectors}`. If it doesn't, or the layout can't be split confidently (flow style, document markers, tabs, unexpected indentation, duplicate keys), fall back to the current full `dump()`.
- **No existing file:** use `dump()` as today.

The write stays atomic (tmp + replace).

**Tests:**
- comments in untouched entries and the header survive an add and a remove;
- a changed entry's own inner comments may be lost, but its neighbours' are kept;
- a flow-style file falls back to a plain dump that still loads correctly;
- the round-trip invariant holds in every test.

### Task 7: RESEARCH.md places custom lookups before Deep Research

**Files:** `plugin/engine/scout/scripts/bootstrap.py` (`_assemble`), tests in `plugin/engine/tests/unit/test_custom_connectors_assembly_wiring.py`.

**Behavior:**
- **RESEARCH:** custom sections are inserted immediately before the first shipped section whose `slot` is `deep-research`. The research phases assemble alphabetically, so commit-notify comes first; Deep Research is where queries happen.
- **No `deep-research` slot:** append, as today.
- **SKILL and DREAMING:** unchanged (append after shipped sections).
- **Backport:** `phase_backport.build_rendered_sections` doesn't depend on order, so no change there; add a test that backport still renders the same custom sections.

**Tests:**
- in RESEARCH, the custom lookup heading index is below the "Deep Research" heading index and above every other research section that sorts after it;
- SKILL ordering is unchanged;
- with no custom connectors, assembled RESEARCH is byte-identical to before. Compare against `_assemble` with `custom={}` on the base behavior: no custom sections means nothing is inserted.

### Task 8: Housekeeping, spec drift and CHANGELOG

**Files:** `plugin/engine/scout/scripts/bootstrap.py` (`CustomApplyResult`, `apply_custom_change`), `custom_connector_ops.py` (`_message`), `plugin/engine/scout/cli.py` (two inline readers), `docs/superpowers/specs/2026-10-02-custom-connectors-design.md`, `plugin/CHANGELOG.md`, plus tests.

1. **Name the drifted file.** `CustomApplyResult` gains `drifted: list[str]`: the brain files whose snapshot didn't match `before` when status is `deferred` due to drift. `apply_custom_change` collects every drifted file before returning `deferred`, instead of returning on the first one. The deferred-by-drift message names them, e.g. "Saved. RESEARCH.md was last assembled from …". Test with a RESEARCH-only drift.
2. **Two inline config readers move to the shared reader.** Both read `scout-config.yaml` with their own code and are in `cli.py`: the function at ~2180, and the `phases backport` command at ~2245-2270. Switch them to `bootstrap.config_from_vault(...)`. Keep their user-facing behavior: the "no vault … run /scout-setup" exit-2 check, and on `yaml.YAMLError` / `UnicodeDecodeError` / `ValueError` print "scout-config.yaml is malformed: …" and exit with `ConfigError.exit_code`. Their existing tests must stay green; add one test showing a non-mapping config now gives the malformed exit, not a traceback.
3. **Spec.** Status line: "implemented — engine in #261, onboarding/install surface tracked in #321". Replace `{{CONNECTOR_FOCUS}}` with `{{CONNECTOR_GUIDANCE}}`. Add the `busy`/exit-4 status to §3.
4. **CHANGELOG.** Under `## [Unreleased]`, add concise `### Added` / `### Fixed` bullets covering Tasks 1–8:
   - the busy status and `--no-wait`;
   - full definitions in `custom list`;
   - health and probes limited to enabled connectors;
   - wrappers seen through by the hook;
   - free-text sanitizing;
   - comments kept in `connectors.custom.yaml`;
   - RESEARCH placement;
   - the drifted file named in the message.

**Verification for every task:**
- the focused test files;
- the full unit suite once;
- the three lint gates;
- `.venv/bin/python -m scout.scripts.connectors_snapshot --check --target scout/connectors.snapshot.json`.
