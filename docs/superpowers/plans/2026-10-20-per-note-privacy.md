# Per-note privacy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Do not start without Jordan's explicit go on this plan.** Every spec
> decision is ruled (spec §2); the plan itself is still awaiting approval.

**Goal:** Add `visibility: normal | private` to any vault note, label every engine read with it, keep private content off shared and third-party surfaces, and reserve `federation_scope` for Stage 2, with a conformance corpus for the Agnes indexer.

**Architecture:** A pure classifier (`scout/kb/privacy.py`) turns (path, frontmatter, policy) into a label. Policy is data: `defaults/privacy.yaml` → generated `privacy.snapshot.json`, plus a vault overlay in `scout-config.yaml`. Every KB read path (new `scoutctl kb` subcommands, the engine `KnowledgeGraph`, the vault `parser.py` shim, cache builders, phases, `scout-ask`) returns labelled results; `owner` and `autonomous` see everything, `agent:*` sees nothing. Output classes (owner-only vs shared) decide where private content may go, enforced by `check-outbound`/`check-query`, the phase rule, lint on shared files, and engine send guards.

**Tech Stack:** Python 3.11+, typer (`scout/cli.py`), PyYAML, pytest, ripgrep (optional, Python fallback), git hooks.

**Spec:** `docs/superpowers/specs/2026-10-20-per-note-privacy-design.md`

## Global Constraints

- `visibility: normal | private`. Unknown value → `private`; unparseable frontmatter → `private`.
- `federation_scope` is reserved: parse (`none | org | [person:<email> | group:<host group id>, …]`, regexes `^person:[^\s@,\[\]]+@[^\s@,\[\]]+$` case-insensitive and `^group:[A-Za-z0-9_-]{1,64}$`), store as `federation_scope_declared`; effective always `none`; `federation_source` always `"reserved"`; no ceilings or `federation.enabled` in v1 (spec §3.2, §12).
- Defaults (spec §5.2): `visibility_defaults` = `knowledge-base/personal/** → private`, `.scout-inbox/** → private`; `local_only.always` = `.scout-logs/** .scout-cache/** .claude/** .venv/** .obsidian/** .pytest_cache/** .mcp.json .env* .scout-state/**` minus `sync_allow` (`.scout-state/schedule.yaml`, `.scout-state/id-map.json`, `.scout-state/privacy-policy.json`); `local_only.default = []`; trading/brokerage paths come from the overlay's `local_only_add`, written by the link step.
- Readers: `owner` and `autonomous` see every file, labelled; every `agent:*` reader sees nothing. Resolution: `SCOUT_READER` if set; else `autonomous` if `SCOUT_MODE` set; else `owner`. `--reader` only narrows (`owner` > `autonomous` > `agent:*`).
- Surfaces (spec §4.2, `privacy.yaml → surfaces`): owner-only `vault, app, telegram_private_chat, agnes_owner_conversation, agnes_personal_memory, vault_sync`; shared `slack, connector_write, connector_query, web_query, agnes_shared, agnes_memory_promotion, peer_agent, public_repo, telegram_group_chat`. Shared files: `drafts/**` plus any note with declared `federation_scope` ≠ `none`.
- Private terms = slugs, `name:` and `aliases:` (≥ 4 chars, whole-word, case-insensitive) of private notes, plus `[[wikilinks]]` resolving to them.
- A denial or a failed check never prints a private file's title, slug, matched term or content — line numbers only.
- Scout never writes privacy defaults into note frontmatter.
- Fixtures anonymised per `plugin/CLAUDE.md` (Alex/Priya/Sam, `example-org/<repo>`, `PROJ-1234`); private fixture files carry `CANARY-PRIV-<n>` tokens.
- Snapshots and the contract corpus are generated or checksum-guarded, never hand-edited.
- Run affected tests only; CI is the gate. Tests that run `bootstrap install` pass `--no-jobs`.
- Lint is `ruff` (from `plugin/engine`) + `mypy scout` + `versioning check`.
- Perf: `kb files` / `kb query` < 300 ms warm on a 3,000-file vault.

## Review Focus

1. **Symlinked path spelling** (`knowledge_base/…` vs `knowledge-base/…`, `./`, `..`): the same label whichever spelling is used. Test in Task 1.
2. **Non-UTF-8 or binary files under `knowledge-base/`** (scan JPGs, PDFs): `kb search`/`files` label or skip them without crashing. Test in Task 4.
3. **A private note's alias used as a Slack search term** (`check-query` with only the alias, no slug): must fail. Test in Task 5.
4. **Wikilink by display name or alias** (`[[Sam Lee]]` resolving through `aliases:`) staged into a shared file: `private-link` must still fire. Test in Task 8.
5. **`scoutctl` missing from PATH when the vault `parser.py` shim runs:** exit non-zero with a message, never read files itself. Test in Task 3.

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
  - `@dataclass(frozen=True) class Policy` with `local_only_always`, `local_only`, `sync_allow`, `visibility_defaults: tuple[tuple[str, str], ...]`, `owner_only_surfaces: frozenset[str]`, `shared_surfaces: frozenset[str]`, `shared_files: tuple[str, ...]`.
  - `load_policy(vault: Path | None) -> Policy` — engine default merged with `scout-config.yaml → privacy:` (keys `local_only_add`, `visibility_defaults`, `shared_files_add`, `surfaces` class overrides). `local_only.always` can't be removed; Stage 2 keys produce a `federation-reserved` warning and are ignored.
  - `@dataclass(frozen=True) class Label` with fields exactly spec §5.4 (`path, visibility, visibility_source, federation_scope, federation_scope_declared, federation_source, local_only, shared_file, warnings`); `Label.to_json() -> dict`.
  - `classify(rel_path: str, text: str | None, policy: Policy) -> Label` — pure; `text` is the file's leading bytes or `None` for unreadable/non-markdown (path defaults only).
  - `normalize_rel(vault: Path, path: str | Path) -> str` — resolves the `knowledge_base` symlink, `./`, `..`; raises `ValueError` outside the vault.
  - `visible_to(label: Label, reader: str) -> bool` — `True` for `owner`/`autonomous`, `False` for `agent:*`.
  - `surface_class(policy: Policy, surface: str) -> Literal["owner_only", "shared"]` — unknown surface → `"shared"`.

- [ ] **Step 1: Write failing table-driven tests** `test_classify_rules` over spec §3.1, §3.2 and §5.3: absent → `normal`/`default`; `Private ` trimmed; `PRIVATE while drafting` → private + warning; broken YAML → private/`forced`; `personal/x.md` with no key → private/`path_default`; `personal/y.md` with `visibility: normal` → normal/`frontmatter`; `.scout-inbox/a.jsonl` (text `None`) → private; `inbox.md`, `Raw/scan.txt` → normal, not local-only; `.venv/x`, `.scout-state/lock` → local-only; `.scout-state/id-map.json` → not local-only; overlay `local_only_add: ["trading/**"]` → `trading/p.md` local-only; `federation_scope: org` → declared `org`, effective `none`, `reserved`, `shared_file: true`; `[person:priya@example.com]` parses; `bogus` → declared `none` + warning; private + org → `privacy-contradiction`; `drafts/r.md` → `shared_file: true`; no inheritance for a sibling. `test_visible_to` (owner/autonomous true, `agent:person:priya@example.com` false). `test_surface_class` incl. unknown → shared. `test_normalize_rel_symlink_spellings` (Review Focus 1).
- [ ] **Step 2: Run** `cd plugin/engine && .venv/bin/python -m pytest tests/unit/test_kb_privacy.py -q` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `privacy.py` with `scout.kb.lint_config.glob_match` and `scout.kb.frontmatter.frontmatter_block`; first-match-wins lists.
- [ ] **Step 4: Snapshot.** `privacy_snapshot.py` writes `privacy.snapshot.json` (`{schema_version: 1, engine_version, policy}`, sorted keys). Test `test_snapshot_matches_yaml` regenerates in tmp and compares bytes.
- [ ] **Step 5: Run** both test files — PASS; `ruff check scout tests && .venv/bin/mypy scout`.
- [ ] **Step 6: Commit** `feat(kb): privacy policy, classifier and snapshot (#453)`.

### Task 2: Contract corpus + `kb classify` / `kb policy`

**Files:**
- Create: `plugin/engine/tests/fixtures/contract/privacy-corpus.json` (`[{path, frontmatter_text, policy_overlay, expected_label}]`, one case per Task 1 row)
- Create: `plugin/engine/tests/unit/test_privacy_corpus.py` (+ `EXPECTED_SHA256` guard, like `test_parser_corpus_checksum.py`)
- Create: `plugin/engine/scout/kb/reader.py`
- Modify: `plugin/engine/scout/cli.py` (kb_app)
- Test: `plugin/engine/tests/unit/test_cli_kb_classify.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `resolve_reader(flag: str | None, env: Mapping[str, str]) -> str` (Global Constraints rule; `typer.BadParameter` on a widening flag or a bad principal).
  - `scoutctl kb classify <path>… [--reader R] [--json]` — for `agent:*` prints `{"path": p, "denied": true}`.
  - `scoutctl kb policy [--json]`.

- [ ] **Step 1: Failing tests:** corpus runner asserts `classify(...).to_json() == expected_label` for every case; checksum test; `test_resolve_reader` (unset + `SCOUT_MODE` → autonomous; both unset → owner; `--reader owner` under `SCOUT_READER=autonomous` → error; `agent:person:priya@example.com` can't widen); CLI tests via `typer.testing.CliRunner`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(kb): privacy contract corpus, kb classify/policy`.

### Task 3: Fixture vault + labelled ontology + vault `parser.py` shim

**Files:**
- Create: `plugin/engine/tests/fixtures/privacy-vault/**` (spec §11.1 table) and `plugin/engine/tests/unit/privacy_vault_helpers.py` (next to `sessions_helpers.py`): `build_privacy_vault(tmp: Path) -> Path` copies it, sets today's dates, and creates the three commits of §11.1
- Modify: `plugin/engine/scout/kb/ontology.py` — port `name_lookup`, `traverse`, `path`, `export_json` from `plugin/templates/knowledge-base/ontology/parser.py`; add `reader: str = "owner"` to `KnowledgeGraph.__init__`; attach `_label` (Task 1 `Label.to_json()`) to every entity in `load()`; `agent:*` loads nothing
- Modify: `plugin/templates/knowledge-base/ontology/parser.py` → shim
- Test: `plugin/engine/tests/unit/test_ontology_privacy.py`, `plugin/engine/tests/unit/test_parser_shim.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: `KnowledgeGraph(schema_path, kb_root, *, reader="owner")` with `query(**filters, visibility: str | None = None)`, `related`, `name_lookup`, `traverse`, `path`, `export_json`; every returned entity has `_label`.

- [ ] **Step 1: Failing tests:** `test_personal_tasks_labelled_for_autonomous` (`query(type="task", domain="personal")` returns `task-renew-permit` with `_label.visibility == "private"` and `task-buy-gift` normal); `test_query_visibility_filter`; `test_agent_reader_loads_nothing`; `test_traverse_includes_private_node_with_label`; parity tests that the ported methods match the template's output on the fixture; `test_shim_execs_scoutctl` and `test_shim_without_scoutctl_fails_closed` (Review Focus 5: exit ≠ 0, stderr names `scoutctl`, no file opened under `knowledge-base/`).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** the port and labels; replace the template body with a shim that `os.execvp`s `scoutctl kb <subcommand>` (maps `query|entity|related|name_lookup|traverse|path|export|stats|validate`), passing `--reader` through.
- [ ] **Step 4: Run** plus existing `tests/unit/test_ontology*.py` and template tests — PASS.
- [ ] **Step 5: Commit** `feat(kb): labelled KnowledgeGraph; vault parser.py delegates to scoutctl`.

### Task 4: Read commands `files`, `search`, `read`, `resolve`, `query`, `related`, `traverse`, `path`

**Files:**
- Create: `plugin/engine/scout/kb/read.py`, `plugin/engine/scout/kb/read_log.py`
- Modify: `plugin/engine/scout/cli.py`
- Test: `plugin/engine/tests/unit/test_cli_kb_read.py`, `plugin/engine/tests/perf/test_kb_files_perf.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `visible_files(vault: Path, reader: str, globs: Sequence[str] = ()) -> list[tuple[str, Label]]`, backed by a stat-keyed `.scout-cache/privacy-index.json`.
  - `search(vault, reader, pattern, globs, fixed: bool, ignore_case: bool) -> list[Match]` (`Match(path, line, text, visibility)`): `rg` over `visible_files` in batches of 500 paths; Python fallback when `rg` is absent.
  - `log_event(vault, *, reader, command, surface, args, returned, denied, private_paths, blocked) -> None` appends spec §7.4 fields to `.scout-logs/kb-reads.jsonl` for non-owner readers and for every blocked check; `args_hash` = sha256 of the joined args.
  - CLI `kb files|search|read|resolve|query|related|traverse|path` per spec §6.1: JSON results carry `label`; text output prefixes private results with `[private] `; `kb read` for `agent:*` exits 3 with `{"denied": true, "reason": "not visible to <reader>"}`.

- [ ] **Step 1: Failing tests:** `test_owner_and_autonomous_see_private_labelled` (every command; canaries present; each private result labelled and `[private]`-prefixed in text); `test_agent_sees_nothing` (every command × `agent:person:priya@example.com`: no canary, no path; `read` exits 3 without a title); `test_binary_and_non_utf8_files` (Review Focus 2); `test_search_without_rg`; `test_log_event_no_content` (no canary or query text in the log line); perf test (generated 3,000 files, warm < 300 ms).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(kb): labelled scoutctl kb read commands`.

### Task 5: `kb delta`, `kb check-outbound`, `kb check-query`

**Files:**
- Modify: `plugin/engine/scout/kb/read.py`, `plugin/engine/scout/kb/git_io.py`, `plugin/engine/scout/cli.py`
- Create: `plugin/engine/scout/kb/outbound.py`
- Test: `plugin/engine/tests/unit/test_kb_delta.py`, `plugin/engine/tests/unit/test_kb_outbound.py`

**Interfaces:**
- Consumes: Task 1 `surface_class`, Task 4 `log_event`.
- Produces:
  - `delta(vault, reader, since: str, with_diff: bool) -> list[Commit]` (`Commit(sha, subject, author, paths: list[tuple[str, Label]], diff: str | None)`); `since` is a rev or a duration (`8h`, `2d`); for `agent:*` returns `[]`.
  - `private_terms(vault, policy) -> list[str]`; `find_mentions(text: str, terms, *, allow_markers: bool) -> list[int]` (1-based lines; `allow_markers=True` honours same-line `%%privacy:allow <slug>%%` for lint; sends pass `False`).
  - `check_outbound(vault, text: str, surface: str) -> list[int]` — `[]` for an owner-only surface; else `find_mentions(..., allow_markers=False)`.
  - CLI `kb delta --since S [--diff]`, `kb check-outbound <file> --surface <id>`, `kb check-query <text> --surface <id>` (exit 1 + line numbers only; `check-query` prints nothing but the exit code and `blocked`).

- [ ] **Step 1: Failing tests:** `delta` labels each path and keeps the private-only commit for autonomous; returns `[]` for `agent:*`; `check-outbound` for `slack`, `connector_write`, `public_repo`, `telegram_group_chat` hits on a slug, a `name:`, an alias and a wikilink, passes for `vault` and `telegram_private_chat`, ignores a marker; `check-query` with only an alias fails for `connector_query` and `web_query` (Review Focus 3); a 3-char name and substrings don't match; outputs contain no matched term; a blocked check writes a log event.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(kb): labelled git delta and outbound/query checks`.

### Task 6: `SCOUT_READER` in runners and launchers; labelled caches

**Files:**
- Modify: `plugin/templates/run-scout.sh.tmpl`, `run-dreaming.sh.tmpl`, `run-research.sh.tmpl` (export next to `SCOUT_MODE`)
- Modify: `plugin/engine/scout/scripts/heartbeat.py`, `schedule_tick.py` (child env), `pre_session_data.py`, `plugin/engine/scout/hooks/kb_pre_filter.py`, `plugin/templates/scripts/vault-freshness.py`, `recurring-task-status.py`
- Modify: `plugin/engine/scout/scripts/vault_drift.py` — report vault `run-*.sh` exporting neither var, and a vault enrichment script that reads the KB without `scoutctl kb`
- Test: extend `test_pre_session_data*.py`, `test_kb_pre_filter*.py`, `test_heartbeat*.py`, `test_vault_drift*.py`, runner render tests

- [ ] **Step 1: Failing tests:** rendered runners contain `export SCOUT_READER=autonomous`; `session-context.json` and `kb-filter.md` built over the fixture as autonomous list `task-renew-permit` marked `[private]` and `task-buy-gift` unmarked; `research_queue_has_open` ignores a private open item; drift reports a fixture `run-extra.sh` without the exports.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** with `classify` / `KnowledgeGraph(reader=resolve_reader(None, os.environ))`. **Step 4: Run** — PASS. Bootstrap tests use `--no-jobs`.
- [ ] **Step 5: Commit** `feat(runners): scheduled runs read as the autonomous reader, with labels`.

### Task 7: Phase migration + eval seed

**Files:**
- Create: `plugin/phases/core/privacy.md` (rules exactly spec §8.1: owner-only vs shared surfaces, query-text rule with `check-query`, the Slack outbound-draft step with `check-outbound --surface slack`, the §7.2 narrowing rules)
- Modify: `plugin/engine/scout/scripts/phase_assembly.py` include list so `core/privacy.md` is in every mode
- Modify: `plugin/phases/core/git-setup.md`, `core/action-items.md`, `core/kb-management.md`, `modes/kb-deep-work.md`, `research/research-targets.md`, `research/knowledge-integration.md` — replace the raw reads in spec §6.3 with `scoutctl kb …`; Slack notification sections use the outbound-draft step; Telegram sections may carry private items; research target selection excludes private entities
- Create: `plugin/engine/tests/eval/scenarios/privacy-canary/` (stub connectors record writes and queries), `plugin/engine/tests/eval/harness/checks/privacy_canary.py`
- Test: phase-assembly tests; brain-pin/token tests; eval tier-0 tests

- [ ] **Step 1: Failing tests:** every assembled mode contains the privacy section; no assembled phase contains `parser.py query`, `git diff HEAD~1` or `rg … knowledge-base` (grep test); on stub transcripts the check scores pass when the permit task's canary is in the daily file and the Telegram payload and absent elsewhere; fail on a canary in a Slack send, a connector write, a connector/web query or a `drafts/` change; flags raw `cat knowledge-base/…`, a Slack send without a preceding `check-outbound`, and `--no-verify`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Edit phases and add the check.** Update brain pins as the existing tests require.
- [ ] **Step 4: Run** affected tests — PASS. Model-backed `scripts/eval.sh run privacy-canary` only with Jordan's OK, locally.
- [ ] **Step 5: Commit** `feat(phases): owner-only vs shared surfaces; read the KB through scoutctl kb; eval seed`.

### Task 8: Lint on shared files, widening guard, engine send guards, doctor

**Files:**
- Modify: `plugin/engine/scout/kb/lint.py`, `lint_config.py` (privacy scope adds `shared_files` globs and `scout-config.yaml`), `hook.py` (privacy checks fail closed for every commit)
- Modify: `plugin/engine/scout/scripts/notify_telegram.py` (classify target: chat id < 0 → `telegram_group_chat`), `plugin/engine/scout/scripts/phase_backport.py` (`check_outbound(..., "public_repo")` on the bundle), `plugin/engine/scout/scripts/bootstrap_doctor.py` (7-day blocked/denied count, yellow)
- Test: `plugin/engine/tests/unit/test_kb_lint_privacy.py`, `test_kb_hook_privacy.py`, `test_notify_telegram*.py`, `test_phase_backport*.py`, doctor tests

**Interfaces:**
- Consumes: Task 5 `private_terms`, `find_mentions`, `check_outbound`; Task 1 `classify`.
- Produces: lint rule ids `private-link`, `private-mention` (block; shared files only), `visibility-value`, `privacy-contradiction`, `federation-reserved`, `frontmatter-unparseable`; `ALLOW_MARKER_RE = r"%%privacy:allow ([a-z0-9._-]+)%%"`; run-health events `privacy.outbound_blocked`, `privacy.check_failed`.

- [ ] **Step 1: Failing tests:** `[[sam]]` staged into `drafts/reply-to-priya.md` or `widget.md` (declared `federation_scope`) blocks an interactive commit, in `report` mode, ignoring `SCOUT_LINT_OVERRIDE`; via alias display name too (Review Focus 4); the same-line marker lets exactly that link through, a marker for another slug or line doesn't; `[[sam]]` added to `priya.md` or the daily file passes; an old link in an edited shared file passes; `kb lint --report` lists markers and old unmarked links in shared files; the widening guard under `SCOUT_MODE` blocks removing `private`, deleting the `visibility` line of a `personal/` note that was set to `private`, setting `federation_scope`, adding a marker, or editing `privacy:`; a checker exception refuses the commit (run: `privacy.check_failed`; interactive: message pointing to `scoutctl doctor`); Telegram canary sends to a private chat and is dropped with the event for a group; a back-port bundle with a canary is refused; doctor shows the count.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(kb): privacy lint on shared files, widening guard and send guards`.

### Task 9: Vault migration, link step, Scout.app badge

**Files:**
- Modify: `plugin/engine/scout/scripts/bootstrap.py` migration list: (a) list notes whose `visibility:` isn't `normal`/`private` and offer a per-note rewrite to `private` (interactive `/scout-update` only; headless prints and skips); (b) print existing private links in shared files; (c) the link step writes `local_only_add` for the vault's trading/brokerage paths when it detects them, with the owner present
- Modify: `plugin/commands/scout-update.md`, `scout-setup` text (one paragraph: `visibility`, the `personal/` and `.scout-inbox/` defaults, `federation_scope` reserved, `scoutctl kb policy`)
- Modify: `apps/macos/` KB browser — lock badge when the label is private (`scoutctl kb classify --reader owner --json`, cached per file stat)
- Test: migration unit tests (never edits headless; edits only the value with consent; link step adds `local_only_add` and is idempotent); Swift test for the badge mapping

- [ ] **Step 1: Failing tests.** **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS (`app-ci` covers Swift).
- [ ] **Step 5: Commit** `feat: normalise legacy visibility markers; local-only trading paths; lock badge` (one commit; it touches both subtrees).

### Task 10: `scout-ask` switch (after #480 merges)

**Files:**
- Modify: `plugin/skills/scout-ask/SKILL.md` per spec §6.3 row 1; `plugin/CHANGELOG.md`
- Test: `tests/unit/test_shell_portability.py` (already globs skills); a grep test that the skill has no `rg … knowledge-base`, `ls knowledge-base`, or `parser.py`

- [ ] **Step 1: Failing grep test.** **Step 2: Edit the skill** (no `--reader`: the ambient reader, spec §6.3; an owner fallback would widen `autonomous` and fail). **Step 3: Run** — PASS. **Step 4: Commit** `feat(skills): scout-ask reads through scoutctl kb`.

### Task 11: Sync labels (with `scoutctl vault sync`)

Blocked on the `vault sync` spec from the Agnes plan. When it lands: sync writes `scoutctl kb policy --json` to `.scout-state/privacy-policy.json` and a label manifest per pushed commit, and refuses locally to push any local-only path (union with the host's list). Tests: a local-only file (including `trading/**` from the overlay) staged for sync is refused before any network call; `personal/` and `.scout-inbox/` files push with `visibility: private` labels; the manifest matches `kb classify` for every pushed path. Task 9's link step must have shipped first (spec §10).

---

## Self-review notes

- Spec §3–§5 → Tasks 1–2; §6 → Tasks 3–5, 7, 10; §7.2 consent → Tasks 7 (narrowing rules) and 8 (widening guard); §7.3 memory capture is Agnes-side (Scout supplies labels and provenance via Task 11); §7.4 audit → Tasks 4, 5, 8; §8 → Tasks 5, 7, 8; §9 → Task 2; §10 P5 → Task 9; P7 → Task 11; §11 → fixtures in Task 3, tests throughout, eval in Task 7.
- Stage 2 (spec §12) has no tasks here: it needs its own approval and plan.
