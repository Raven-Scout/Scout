# Per-note privacy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Do not start until Jordan has approved the spec.** D5 and D6 are still
> pending (spec §13); if Jordan adopts the §13 drafts, Tasks 1, 4, 6, 7 and 8
> change (output classes instead of a read-gate for `autonomous`). Re-read the
> approved spec's §2 first and update this plan to match.

**Goal:** Add `visibility:` and `federation_scope:` to any vault note, enforced through one set of `scoutctl kb` read commands, with outbound guards and a conformance corpus for the Agnes indexer.

**Architecture:** A pure classifier (`scout/kb/privacy.py`) turns (path, frontmatter, policy) into a label. Policy is data: `defaults/privacy.yaml` → generated `privacy.snapshot.json`, plus a vault overlay in `scout-config.yaml`. Every KB read path (new `scoutctl kb` subcommands, the engine `KnowledgeGraph`, the vault `parser.py` shim, cache builders, phases, `scout-ask`) filters by reader through that classifier; lint, commit-msg and send guards catch leaks on the way out.

**Tech Stack:** Python 3.11+, typer (`scout/cli.py`), PyYAML, pytest, ripgrep (optional, Python fallback), git hooks.

**Spec:** `docs/superpowers/specs/2026-10-20-per-note-privacy-design.md`

## Global Constraints

- Fields: `visibility: normal | private`; `federation_scope: none | org | [person:<email> | group:<host group id>, …]`; regexes `^person:[^\s@,\[\]]+@[^\s@,\[\]]+$` (case-insensitive) and `^group:[A-Za-z0-9_-]{1,64}$` (spec §3.3).
- Unknown `visibility` → `private`; unparseable `federation_scope` → `none`; unparseable frontmatter → `private` + `none`.
- Defaults: `visibility: normal`, `federation_scope: none`. v1: `federation_scope` is reserved — parsed and stored as `federation_scope_declared`, effective always `none`, every `agent:*` reader denied; no ceilings or `federation.enabled` in v1 (spec §3.2, §12).
- Scout never writes privacy defaults into note frontmatter.
- Reader resolution: `SCOUT_READER` if set; else `autonomous` if `SCOUT_MODE` set; else `owner`. `--reader` only narrows. Order of width: `owner` > `autonomous` > `agent:*`.
- `--include-private` accepted only for reader `owner`.
- A denial never prints a private file's title, slug or content.
- Fixtures anonymised per `plugin/CLAUDE.md` (Alex/Priya/Sam, `example-org/<repo>`, `PROJ-1234`); private fixture files carry `CANARY-PRIV-<n>` tokens.
- Snapshots and the contract corpus are generated or checksum-guarded, never hand-edited.
- Run affected tests only; CI is the gate. Tests that run `bootstrap install` pass `--no-jobs`.
- Lint is `ruff` (from `plugin/engine`) + `mypy scout` + `versioning check`.
- Perf: `kb files` / `kb query` < 300 ms warm on a 3,000-file vault.

## Review Focus

1. **Symlinked path spelling** (`knowledge_base/…` vs `knowledge-base/…`, `./`, `..`): a reader must get the same label whichever spelling it uses. Test in Task 1.
2. **Non-UTF-8 or binary files under `knowledge-base/`** (scan JPGs, PDFs): `kb search`/`files` must skip or label them without crashing. Test in Task 4.
3. **A private note renamed or moved via `kb-move.sh` in a run's commit:** `kb delta` must not reveal the old or new path. Test in Task 5.
4. **Wikilink by display name or alias** (`[[Sam Lee]]` resolving through `aliases:`): `private-link` must still fire. Test in Task 8.
5. **`scoutctl` missing from PATH when the vault `parser.py` shim runs:** must exit non-zero with a message, never read files itself. Test in Task 3.

---

### Task 1: Policy file, snapshot and classifier

**Files:**
- Create: `plugin/engine/scout/defaults/privacy.yaml` (contents exactly spec §5.2)
- Create: `plugin/engine/scout/kb/privacy.py`
- Create: `plugin/engine/scout/scripts/privacy_snapshot.py` (pattern: `connectors_snapshot.py`)
- Create: `plugin/engine/scout/privacy.snapshot.json` (generated)
- Test: `plugin/engine/tests/unit/test_kb_privacy.py`, `plugin/engine/tests/unit/test_privacy_snapshot.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Policy` with `local_only_always`, `local_only`, `sync_allow`, `visibility_defaults: tuple[tuple[str,str],...]`, `outbound_surfaces: tuple[str,...]`.
  - `load_policy(vault: Path | None) -> Policy` — engine default merged with `scout-config.yaml → privacy:` (keys `local_only_add`, `local_only_remove: [{glob, reason}]`, `visibility_defaults`). `local_only.always` cannot be removed; Stage 2 keys in an overlay produce a `federation-reserved` warning and are ignored.
  - `@dataclass(frozen=True) class Label` with fields exactly spec §5.5 (`path, visibility, visibility_source, federation_scope, federation_scope_declared, federation_source, local_only, warnings`; `federation_source` is always `"reserved"` in v1); `Label.to_json() -> dict`.
  - `FederationScope = Literal["none","org"] | tuple[str, ...]`.
  - `classify(rel_path: str, text: str | None, policy: Policy) -> Label` — pure; `text` is the file's leading bytes (frontmatter region) or `None` for unreadable.
  - `normalize_rel(vault: Path, path: str | Path) -> str` — resolves the `knowledge_base` symlink, `./`, `..`; raises `ValueError` outside the vault.
  - `visible_to(label: Label, reader: str, policy: Policy) -> bool`.

- [ ] **Step 1: Write failing table-driven tests** `test_classify_rules` parametrized over every rule in spec §3.1, §3.2 and §5.4 (absent, `normal`, `Private ` trimmed, `PRIVATE while drafting` → private + warning, broken YAML → private/none/`forced`, `federation_scope: org` → declared `org`, effective `none`, source `reserved`; `[person:priya@example.com]` parses; `federation_scope: bogus` → declared `none` + warning; private + org → `privacy-contradiction` warning; local-only path → `local_only: true`; overlay `visibility_defaults` glob applies, no inheritance for a sibling). `test_visible_to` matrix: owner sees all; autonomous sees non-private; `agent:person:priya@example.com` sees nothing, even for `federation_scope: org` and with an overlay `federation.enabled: true`. `test_normalize_rel_symlink_spellings` (Review Focus 1).
- [ ] **Step 2: Run** `cd plugin/engine && .venv/bin/python -m pytest tests/unit/test_kb_privacy.py -q` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `privacy.py`. Use `scout.kb.lint_config.glob_match` for globs and `scout.kb.frontmatter.frontmatter_block` for the block; first-match-wins lists.
- [ ] **Step 4: Snapshot.** `privacy_snapshot.py` writes `privacy.snapshot.json` (`{schema_version: 1, engine_version, policy}` sorted keys). Test `test_snapshot_matches_yaml` regenerates in tmp and compares bytes.
- [ ] **Step 5: Run** both test files — Expected: PASS. Run `ruff check scout tests && .venv/bin/mypy scout`.
- [ ] **Step 6: Commit** `feat(kb): privacy policy, classifier and snapshot (#453)`.

### Task 2: Contract corpus + `kb classify` / `kb policy`

**Files:**
- Create: `plugin/engine/tests/fixtures/contract/privacy-corpus.json` (`[{path, frontmatter_text, policy_overlay, expected_label}]`, one case per Task 1 row)
- Create: `plugin/engine/tests/unit/test_privacy_corpus.py` (+ `EXPECTED_SHA256` guard, like `test_parser_corpus_checksum.py`)
- Create: `plugin/engine/scout/kb/reader.py`
- Modify: `plugin/engine/scout/cli.py` (kb_app)
- Test: `plugin/engine/tests/unit/test_cli_kb_classify.py`

**Interfaces:**
- Consumes: Task 1 `classify`, `load_policy`, `normalize_rel`.
- Produces:
  - `resolve_reader(flag: str | None, env: Mapping[str,str]) -> str` in `reader.py` (Global Constraints rule; raises `typer.BadParameter` on a widening flag or bad principal).
  - `scoutctl kb classify <path>… [--reader R] [--json]` — a non-visible path prints `{"path": p, "denied": true}`.
  - `scoutctl kb policy [--json]`.

- [ ] **Step 1: Failing tests:** corpus runner asserts `classify(...).to_json() == expected_label` for every case; checksum test; `test_resolve_reader` (unset+SCOUT_MODE → autonomous, both unset → owner, `--reader owner` under `SCOUT_READER=autonomous` → error, `agent:person:priya@example.com` can't widen); CLI test via `typer.testing.CliRunner` on a tmp vault.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** `reader.py` and the two commands.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(kb): privacy contract corpus, kb classify/policy`.

### Task 3: Fixture vault + reader-aware ontology + vault `parser.py` shim

**Files:**
- Create: `plugin/engine/tests/fixtures/privacy-vault/**` (spec §11.1 table) and `plugin/engine/tests/unit/privacy_vault_helpers.py` (next to `sessions_helpers.py`) (`build_privacy_vault(tmp: Path) -> Path` copies it, sets `today` dates, and creates the three commits of §11.1)
- Modify: `plugin/engine/scout/kb/ontology.py` — port `name_lookup`, `traverse`, `path`, `export_json` from `plugin/templates/knowledge-base/ontology/parser.py`; add `reader: str = "owner"` and `include_private: bool = False` to `KnowledgeGraph.__init__`; filter in `load()` via `classify`
- Modify: `plugin/templates/knowledge-base/ontology/parser.py` → shim
- Test: `plugin/engine/tests/unit/test_ontology_privacy.py`, `plugin/engine/tests/unit/test_parser_shim.py`

**Interfaces:**
- Consumes: Task 1, Task 2 `resolve_reader`.
- Produces: `KnowledgeGraph(schema_path, kb_root, *, reader="owner", include_private=False)` with `query`, `related`, `name_lookup`, `traverse`, `path`, `export_json`; non-owner readers never see or walk through private nodes. `include_private=True` with a non-owner reader raises `PermissionError`.

- [ ] **Step 1: Failing tests:** `test_private_task_absent_for_autonomous` (`query(type="task", domain="personal")` omits `task-renew-permit`, keeps `task-buy-gift`); `test_owner_sees_private`; `test_traverse_skips_private_node` (path priya→sam→x is None for autonomous); `test_name_lookup_hides_private_alias`; parity tests that the ported methods match the template's output on the fixture for owner; `test_shim_execs_scoutctl` and `test_shim_without_scoutctl_fails_closed` (Review Focus 5: exit ≠ 0, stderr names `scoutctl`, no file opened under `knowledge-base/`).
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** the port and filter, then replace the template body with a shim that `os.execvp`s `scoutctl kb <subcommand>` (map `query|entity|related|name_lookup|traverse|path|export|stats|validate`), passing `--reader` through.
- [ ] **Step 4: Run** plus existing `tests/unit/test_ontology*.py` and template tests — Expected: PASS.
- [ ] **Step 5: Commit** `feat(kb): reader-aware KnowledgeGraph; vault parser.py delegates to scoutctl`.

### Task 4: Read commands `files`, `search`, `read`, `resolve`, `query`, `related`, `traverse`, `path`

**Files:**
- Create: `plugin/engine/scout/kb/read.py`, `plugin/engine/scout/kb/read_log.py`
- Modify: `plugin/engine/scout/cli.py`
- Test: `plugin/engine/tests/unit/test_cli_kb_read.py`, `plugin/engine/tests/perf/test_kb_files_perf.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `visible_files(vault: Path, reader: str, globs: Sequence[str] = ()) -> list[str]`, backed by a stat-keyed `.scout-cache/privacy-index.json`.
  - `search(vault, reader, pattern, globs, fixed: bool, ignore_case: bool) -> list[Match]` (`Match(path, line, text)`): `rg` over `visible_files` in batches of 500 paths; Python fallback when `rg` is absent.
  - `log_read(vault, reader, command, args, returned, denied) -> None` appends spec §7.3 fields to `.scout-logs/kb-reads.jsonl` for non-owner readers only; `args_hash` = sha256 of the joined args.
  - CLI: `kb files|search|read|resolve|query|related|traverse|path` per spec §6.1; `kb read` on a denied path exits 3 with `{"denied": true, "reason": "not visible to <reader>"}`.

- [ ] **Step 1: Failing tests:** `test_no_canary_for_non_owner` parametrized over every command × readers `autonomous`, `agent:person:priya@example.com` — stdout never contains `CANARY-PRIV`; `test_owner_sees_canaries`; `test_read_denied_exit_3_no_title`; `test_binary_and_non_utf8_files` (Review Focus 2: a JPG and a latin-1 `.md` under `knowledge-base/` don't crash `files`/`search`); `test_search_without_rg`; `test_read_log_no_content`; perf test marked like existing perf tests (generated 3,000 files, warm < 300 ms).
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(kb): reader-gated scoutctl kb read commands`.

### Task 5: `kb delta` and `kb check-outbound`

**Files:**
- Modify: `plugin/engine/scout/kb/read.py`, `plugin/engine/scout/kb/git_io.py`, `plugin/engine/scout/cli.py`
- Create: `plugin/engine/scout/kb/outbound.py`
- Test: `plugin/engine/tests/unit/test_kb_delta.py`, `plugin/engine/tests/unit/test_kb_outbound.py`

**Interfaces:**
- Produces:
  - `delta(vault, reader, since: str, with_diff: bool) -> list[Commit]` (`Commit(sha, subject, author, paths: list[str], diff: str | None)`). `since` is a rev or a duration (`8h`, `2d`). A commit whose paths are all invisible is dropped; renames count both sides (Review Focus 3).
  - `private_terms(vault, policy) -> list[str]` — slugs and `name:`/`aliases:` (≥ 4 chars) of private notes.
  - `find_mentions(text: str, terms) -> list[int]` — 1-based line numbers; whole-word, case-insensitive; also `[[wikilinks]]` resolving to a private note.
  - CLI `kb delta --since S [--diff]`, `kb check-outbound <file>` (exit 1 + line numbers only).

- [ ] **Step 1: Failing tests:** private-only commit absent; mixed commit lists only `rollout.md`; diff has no canary; `test_rename_of_private_note_hidden`; `check-outbound` hits on a slug, a `name:`, an alias, a wikilink, and stays quiet on a 3-char name and on substrings; output contains no matched term.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(kb): reader-gated git delta and outbound check`.

### Task 6: `SCOUT_READER` in runners and launchers; cache builders

**Files:**
- Modify: `plugin/templates/run-scout.sh.tmpl`, `run-dreaming.sh.tmpl`, `run-research.sh.tmpl` (export next to `SCOUT_MODE`)
- Modify: `plugin/engine/scout/scripts/heartbeat.py`, `schedule_tick.py` (child env), `pre_session_data.py`, `plugin/engine/scout/hooks/kb_pre_filter.py`, `plugin/templates/scripts/vault-freshness.py`, `recurring-task-status.py`
- Modify: `plugin/engine/scout/scripts/vault_drift.py` — report vault `run-*.sh` exporting neither var, and a vault enrichment script that uses `rglob` without `scoutctl kb`
- Test: extend `test_pre_session_data*.py`, `test_kb_pre_filter*.py`, `test_heartbeat*.py`, `test_vault_drift*.py`, runner render tests

- [ ] **Step 1: Failing tests:** rendered runners contain `export SCOUT_READER=autonomous`; `session-context.json` and `kb-filter.md` built over the fixture as autonomous contain no canary and still list `task-buy-gift`; `research_queue_has_open` ignores a private open item; drift reports a fixture `run-extra.sh` without the exports.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** (call `visible_to`/`KnowledgeGraph(reader=resolve_reader(None, os.environ))`). **Step 4: Run** — PASS. Bootstrap tests use `--no-jobs`.
- [ ] **Step 5: Commit** `feat(runners): scheduled runs read the vault as the autonomous reader`.

### Task 7: Phase migration + eval seed

**Files:**
- Create: `plugin/phases/core/privacy.md` (rules exactly spec §8.1, plus the §8.4 outbound-draft step)
- Modify: phase assembly so `core/privacy.md` is included in every mode (`plugin/engine/scout/scripts/phase_assembly.py` include list)
- Modify: `plugin/phases/core/git-setup.md`, `core/action-items.md`, `core/kb-management.md`, `modes/kb-deep-work.md`, `research/research-targets.md`, `research/knowledge-integration.md` — replace the raw reads listed in spec §6.3 with `scoutctl kb …`; the notification sections write `.scout-cache/outbound-draft.md` and run `kb check-outbound` before sending
- Create: `plugin/engine/tests/eval/scenarios/privacy-canary/`, `plugin/engine/tests/eval/harness/checks/privacy_canary.py`
- Test: phase-assembly tests; brain-pin/token tests; eval tier-0 tests

- [ ] **Step 1: Failing tests:** every assembled mode contains the privacy section; no assembled phase contains `parser.py query`, `git diff HEAD~1` or `rg … knowledge-base` (grep test); the eval check scores 0 on a stub transcript containing `CANARY-PRIV-1` in the daily file, and flags a raw `cat knowledge-base/…` and `--no-verify` in a transcript.
- [ ] **Step 2: Run** — FAIL. **Step 3: Edit phases and add the check.** Update brain pins as the existing tests require.
- [ ] **Step 4: Run** affected tests — PASS. Model-backed `scripts/eval.sh run privacy-canary` only with Jordan's OK, locally.
- [ ] **Step 5: Commit** `feat(phases): read the KB only through scoutctl kb; privacy rule + eval seed`.

### Task 8: Lint checks, widening guard, commit-msg hook, Telegram guard, doctor

**Files:**
- Modify: `plugin/engine/scout/kb/lint.py`, `lint_config.py` (privacy scope adds `drafts/**`, `scout-config.yaml`), `hook.py` (fail-closed path when `SCOUT_MODE` set; install `commit-msg`)
- Modify: `plugin/engine/scout/scripts/notify_telegram.py`, `plugin/engine/scout/scripts/bootstrap_doctor.py` (7-day denied-read count, yellow)
- Modify: `plugin/engine/scout/cli.py` (`hook commit-msg`)
- Test: `plugin/engine/tests/unit/test_kb_lint_privacy.py`, `test_kb_hook_privacy.py`, `test_notify_telegram*.py`, doctor tests

**Interfaces:**
- Consumes: Task 5 `private_terms`, `find_mentions`; Task 1 `classify`.
- Produces: lint rule ids `private-link`, `private-mention`, `visibility-value`, `privacy-contradiction`, `frontmatter-unparseable`, `federation-reserved` (severities per spec §8.2); `ALLOW_MARKER_RE = r"%%privacy:allow ([a-z0-9._-]+)%%"`; run-health events `privacy.outbound_blocked`, `privacy.check_failed`.

- [ ] **Step 1: Failing tests:** each rule fires/quiet; `private-link` via alias display name (Review Focus 4); `private-link`/`private-mention` block an **interactive** commit too, in `report` mode, and ignore `SCOUT_LINT_OVERRIDE`; same-line `%%privacy:allow sam%%` lets exactly that link through, a marker for another slug or another line doesn't; only added/changed lines are checked (an old link in an edited file passes); `kb lint --report` lists markers and old unmarked links; widening guard under `SCOUT_MODE` blocks removing `private`, widening `federation_scope`, adding a marker, or editing `privacy:`; checker exception → commit refused (run: `privacy.check_failed` event; interactive: message pointing to `scoutctl doctor`); commit-msg naming a private note refused under `SCOUT_MODE`; Telegram message with a private name dropped + event; doctor shows the count.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(kb): privacy lint, widening guard and outbound send guards`.

### Task 9: Vault migration + Scout.app badge

**Files:**
- Create: a bootstrap migration in `plugin/engine/scout/scripts/bootstrap.py`'s migration list: list notes whose `visibility:` isn't `normal`/`private`, offer per-note rewrite to `private` (interactive `/scout-update` only; prints and skips when headless)
- Modify: `plugin/commands/scout-update.md`, `scout-setup` text (one paragraph on the fields + `scoutctl kb policy`)
- Modify: `apps/macos/` KB browser — lock badge when the file's label is private (shell out to `scoutctl kb classify --reader owner --json`, cached per file stat)
- Test: migration unit test (never edits headless; edits only the value with consent); Swift test for the badge mapping

- [ ] **Step 1: Failing tests.** **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS (`app-ci` covers Swift).
- [ ] **Step 5: Commit** `feat: normalise legacy visibility markers; lock badge in the KB browser` (one commit; it touches both subtrees).

### Task 10: `scout-ask` switch (after #480 merges)

**Files:**
- Modify: `plugin/skills/scout-ask/SKILL.md` per spec §6.3 row 1; `plugin/CHANGELOG.md`
- Test: `tests/unit/test_shell_portability.py` (already globs skills); add a grep test that the skill has no `rg … knowledge-base`, `ls knowledge-base`, or `parser.py`

- [ ] **Step 1: Failing grep test.** **Step 2: Edit the skill** (`--reader "${SCOUT_READER:-owner}"` on every call). **Step 3: Run** — PASS. **Step 4: Commit** `feat(skills): scout-ask reads through scoutctl kb`.

### Task 11: Sync labels (with `scoutctl vault sync`)

Blocked on the `vault sync` spec from the Agnes plan. When it lands: sync writes `scoutctl kb policy --json` to `.scout-state/privacy-policy.json` and a label manifest per pushed commit; refuses locally to push any local-only path (union with the host's list). Tests: a local-only file staged for sync is refused before any network call; the manifest matches `kb classify` for every pushed path.

---

## Self-review notes

- Spec §3–§5 → Tasks 1–2; §6 → Tasks 3–5, 7, 10; §7.2 consent → Task 8 widening guard; §7.3 audit → Tasks 4, 8; §8 → Tasks 5, 7, 8; §9 → Task 2; §10 P5 → Task 9; P7 → Task 11; §11 → fixtures in Task 3, tests throughout, eval in Task 7.
- Stage 2 (spec §12) has no tasks here: it needs its own approval and plan. v1 only parses and stores `federation_scope`.
