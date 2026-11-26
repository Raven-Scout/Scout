# Changes Map — Plan 1: Engine Ledger and Capture

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The engine records every Scout self-change and every owner-engagement signal in append-only ledgers, and serves both as versioned `scoutctl … --json` documents.

**Architecture:** A new `scout.changes` package owns the row schemas, the two stores (run-writable `.scout-logs/` and the owner-only `~/.local/state/scout/changes/`), an idempotent git scanner that turns commits into change rows, and reliance derivation that the existing `session-tokens` Stop hook calls at session end. The runner templates call `scoutctl changes scan` after each scheduled session, outside the sandbox. Verdicts, phases, the app and the digest are Plans 2–5.

**Tech Stack:** Python 3 (engine, typer CLI, pytest), bash runner templates.

**Spec:** `docs/superpowers/specs/2026-11-17-changes-map-design.md` (§3, §6.1–6.3, §6.5 git half, §12 step 1). Read it first.

## The five plans

| Plan | Delivers | Depends on |
|---|---|---|
| **1 (this)** | Ledgers, git scanner (also the git backfill), `engagement record`, reliance capture + consent, JSON contracts | — |
| 2 | Verdict engine + calibration (§7, §7a); phase edits: Slack 7-day re-read and signal recording, `dm-sent`, `red-carried`, test-loop step, governance path (§8), Step 1e removal, commit trailers | 1 |
| 3 | App "Changes" tab v1 (§9.1 v1 rows), remove Proposals + Wishlist tabs, contract copies + `contract.yml` steps | 1, 2 |
| 4 | `scoutctl changes digest`, weekly digest DM, `/scout-status` section (§10, §9.2 v1) | 2 |
| 5 | Feedback backfill from the DM archive; opt-in transcript backfill (§6.5) | 1 |

## Global Constraints

- Ledger rows carry `schema_version: 1`; a reader ignores unknown keys; a breaking change bumps the version.
- No content leaves through a row: no prompt text, no message text, no path outside the vault, no project name. Reliance rows hold surface + hashed session id + timestamp only.
- Owner-sourced kinds (`session-reliance`, `app-edit`, `inline-comment`, `approval`) are written **only** to `~/.local/state/scout/changes/`, which must be a protected engine root (runs can't write it).
- A process is an owner session only when `SCOUT_MODE` is unset, `SCOUT_READER` is unset or `owner`, and `run_context.resolve(...)` finds no run mode.
- Hooks never raise and never block a session (match `session_tokens.run`'s behavior).
- Change `summary` ≤ 120 characters.
- Out-of-vault reliance is recorded only after the owner answered the consent question with yes. The answer lives in the owner store, not `scout-config.yaml`, because a run can write `scout-config.yaml`. (This tightens spec §6.3, which named `scout-config.yaml`.)
- Never run `release-app.sh` or `release-plugin.sh`.
- Tests run from `plugin/engine`: `.venv/bin/python -m pytest <path> -v`. `tests/conftest.py` scrubs `SCOUT_*` env vars before each test.

## Review Focus

1. **A scheduled run tries to record owner engagement or consent** (`SCOUT_MODE` set) → refused with exit 2, nothing written anywhere. Pinned in Task 3.
2. **The scanner runs twice over overlapping windows**, or the backfill re-scans history → no duplicate change rows. Pinned in Task 2.
3. **A torn or garbage JSONL line** (crash mid-append, hand edit) → readers skip it and report a count, never raise; the Stop hook still exits 0. Pinned in Tasks 1 and 4.
4. **An out-of-vault session with no consent answer** → no reliance row for it; an in-vault session in the same state is still counted. Pinned in Task 4.
5. **Commits that aren't self-changes** (trading ticks, briefing content edits to `knowledge-base/` notes or `action-items/`) → no change row, so the map isn't flooded. Pinned in Task 2.

---

### Task 1: Row schemas and the two stores

**Files:**
- Create: `plugin/engine/scout/changes/__init__.py`, `plugin/engine/scout/changes/models.py`, `plugin/engine/scout/changes/store.py`
- Modify: `plugin/engine/scout/kb/hook_paths.py` (add `CHANGES_OWNER_NAME = "changes"`; append `state / CHANGES_OWNER_NAME` in `engine_roots`, beside `RUN_BROKERS_NAME`; add it to the protected-name tuple near line 602)
- Test: `plugin/engine/tests/unit/test_changes_store.py`

**Interfaces:**
- Produces (`scout.changes.models`):
  - `SCHEMA_VERSION = 1`
  - `CHANGE_KINDS = frozenset({"brain-edit","driver","phase","kb-structure","wishlist-done","upgrade","rollback"})`
  - `WHY_TYPES = frozenset({"pattern","owner-ask","wishlist","research","unknown"})`
  - `LANES = frozenset({"briefing","consolidation","dreaming","research","work","interactive","update"})`
  - `OWNER_KINDS = frozenset({"session-reliance","app-edit","inline-comment","approval"})`
  - `RUN_KINDS = frozenset({"dm-reply","dm-reaction","dm-sent","red-carried"})`
  - `SURFACES = frozenset({"slack-dm","telegram","kb","action-items","skill","scoutctl","vault-session","app"})`
  - `SENTIMENTS = frozenset({"positive","negative","neutral"})`
  - `@dataclass(frozen=True) Why(type: str, ref: str, gloss: str | None = None, inferred: bool = False)`
  - `@dataclass(frozen=True) Change(id: str, ts: datetime, kind: str, summary: str, why: Why, commit: str, files: tuple[str, ...], targets: tuple[str, ...] = (), governance: bool = False, author_lane: str = "interactive", backfilled: bool = False)`; `Change.to_row() -> dict`, `Change.from_row(row: dict) -> Change` (raises `ValueError` on any field outside its set/shape). `id` is `"chg-" + commit[:12]`.
  - `@dataclass(frozen=True) Signal(ts: datetime, kind: str, surface: str, ref: str | None = None, sentiment: str | None = None, session: str | None = None, vault_key: str | None = None)`; same `to_row` / `from_row`.
- Produces (`scout.changes.store`):
  - `owner_dir(env: Mapping[str, str]) -> Path`: `~/.local/state/scout/changes/` (same HOME logic as `scout.sandbox.broker.owner_dir`)
  - `vault_key(vault: Path) -> str`: sha256 of the resolved vault path, first 16 hex chars (owner rows from several vaults share one folder)
  - `is_owner_session(env: Mapping[str, str]) -> bool`: `SCOUT_MODE` unset and `SCOUT_READER` in (unset, `"owner"`)
  - `append_change(vault: Path, change: Change) -> None` → `<vault>/.scout-logs/changes.jsonl`
  - `read_changes(vault: Path) -> tuple[list[Change], int]` (rows, skipped-bad-row count)
  - `append_signal(signal: Signal, *, vault: Path, env: Mapping[str, str]) -> None`: owner kinds → `owner_dir/engagement.jsonl` with `vault_key` set; run kinds → `<vault>/.scout-logs/engagement.jsonl`. Raises `PermissionError` for an owner kind when `not is_owner_session(env)`.
  - `read_signals(vault: Path, env: Mapping[str, str]) -> tuple[list[Signal], int]`: both files merged, owner rows filtered to this vault's key, sorted by `ts`.
  - Appends use one `write()` of one line with `O_APPEND`, file mode 0600 in the owner dir.

- [ ] **Step 1: Write the failing tests**

```python
def test_change_round_trips(tmp_path): ...
    # Change(...) -> to_row -> from_row == original; row["schema_version"] == 1

def test_change_rejects_unknown_kind_and_long_summary():
    # from_row with kind="vibes" -> ValueError; summary of 121 chars -> ValueError

def test_owner_kind_routed_to_owner_dir(tmp_path):
    env = {"HOME": str(tmp_path / "home")}
    append_signal(Signal(ts=NOW, kind="session-reliance", surface="kb", session="ab12"), vault=vault, env=env)
    assert (tmp_path / "home/.local/state/scout/changes/engagement.jsonl").exists()
    assert not (vault / ".scout-logs/engagement.jsonl").exists()

def test_owner_kind_refused_in_a_run(tmp_path):
    env = {"HOME": str(tmp_path / "home"), "SCOUT_MODE": "dreaming"}
    with pytest.raises(PermissionError):
        append_signal(Signal(ts=NOW, kind="approval", surface="app"), vault=vault, env=env)
    assert not any((tmp_path / "home").rglob("*.jsonl"))

def test_run_kind_goes_to_vault_logs(tmp_path):
    # kind="dm-reply" with SCOUT_MODE set -> lands in <vault>/.scout-logs/engagement.jsonl

def test_read_signals_filters_other_vaults_and_skips_torn_lines(tmp_path):
    # owner file holds a row for another vault_key and a line '{"ts": "2026-11' (torn)
    # -> read_signals returns only this vault's rows, skipped == 1, no exception

def test_owner_dir_is_a_protected_engine_root(tmp_path):
    env = {"HOME": str(tmp_path)}
    assert tmp_path / ".local/state/scout/changes" in hook_paths.engine_roots(env)
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_changes_store.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'scout.changes'`

- [ ] **Step 3: Implement `models.py`, `store.py` and the `hook_paths` additions as specified in Interfaces**

- [ ] **Step 4: Run the new tests plus the existing hook-path tests**

Run: `.venv/bin/python -m pytest tests/unit/test_changes_store.py tests/unit -k "hook_paths or changes_store" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add plugin/engine/scout/changes plugin/engine/scout/kb/hook_paths.py plugin/engine/tests/unit/test_changes_store.py
git commit -m "feat(changes): change and engagement ledgers with an owner-only store"
```

---

### Task 2: The git scanner (change capture + git backfill)

**Files:**
- Create: `plugin/engine/scout/changes/scan.py`
- Test: `plugin/engine/tests/unit/test_changes_scan.py` (builds a throwaway git repo in `tmp_path`)

**Interfaces:**
- Consumes: `Change`, `Why`, `append_change`, `read_changes` (Task 1)
- Produces:
  - `parse_trailers(message: str) -> dict[str, str]`: reads `Scout-Change-Why`, `Scout-Change-Summary`, `Scout-Change-Targets` (comma list), `Scout-Change-Governance` (`yes`/`no`)
  - `lane_of(subject: str) -> str | None`: prefix `briefing [`, `consolidation [`, `dreaming [`, `research [`, `work [` → that lane; `scout:` / `fix(vault)` / anything else typed by a person → `interactive`; `scout-update:` / `upgrade` / `chore(scout-update)` → `update`; `trading [` → `None` (never a change)
  - `classify(paths: list[tuple[str, str]], subject: str, diff_of: Callable[[str], str]) -> str | None`. `paths` is git `--name-status` pairs. First match wins: lane `update` → `upgrade`; any of `SKILL.md`, `DREAMING.md`, `RESEARCH.md` modified → `brain-edit`; an added/modified file under `scripts/` → `driver`; a rename (`R…`) under `knowledge-base/` → `kb-structure`; a file under `docs/wishlist/` whose diff adds `status: done` → `wishlist-done`; subject starts `Revert ` → `rollback`; otherwise `None`.
  - `infer_why(subject: str, body: str) -> Why`: `Pattern #N` → `Why("pattern", "pattern-N", inferred=True)`; `wishlist —` → `Why("wishlist", <slug from a docs/wishlist path in the commit>, inferred=True)`; else `Why("unknown", "", inferred=True)`
  - `scan(vault: Path, *, since: datetime | None, backfilled: bool = False) -> list[Change]`: walks `git -C vault log --no-merges --name-status --format=<sha, ISO date, subject, body>` from `since` (all history when `None`), skips SHAs already in `read_changes`, builds and appends one `Change` per classified commit, returns the new ones. Summary = trailer, else the subject with its lane prefix and `[HH:MM]:` stripped, cut to 120 chars. Targets from the trailer, else the inferred pattern ref. Governance from the trailer, else `False`.

- [ ] **Step 1: Write the failing tests**

```python
def test_brain_edit_with_trailers(repo):
    commit(repo, "SKILL.md", "dreaming [22:12]: SKILL.md self-edit — x\n\nScout-Change-Why: pattern-288\nScout-Change-Summary: Flags leave labels past their end date")
    [c] = scan(repo, since=None)
    assert (c.kind, c.why.type, c.why.ref, c.why.inferred, c.author_lane) == ("brain-edit", "pattern", "pattern-288", False, "dreaming")
    assert c.summary == "Flags leave labels past their end date"

def test_driver_without_trailer_infers_why(repo):
    commit(repo, "scripts/pr-state-check.py", "dreaming [22:16]: wishlist/driver — closes Pattern #271")
    [c] = scan(repo, since=None)
    assert (c.kind, c.why.ref, c.why.inferred, c.targets) == ("driver", "pattern-271", True, ("pattern-271",))

def test_scan_is_idempotent(repo):
    commit(repo, "DREAMING.md", "dreaming [18:00]: DREAMING.md self-edit — y")
    assert len(scan(repo, since=None)) == 1
    assert scan(repo, since=None) == []
    assert len(read_changes(repo)[0]) == 1

def test_non_changes_produce_no_rows(repo):
    commit(repo, "knowledge-base/projects/x/x.md", "consolidation [11:05]: closed EHYG")
    commit(repo, "action-items/action-items-2026-11-17.md", "briefing [2026-11-17]: items")
    commit(repo, "trading/log.md", "trading [15:31]: NAV $45.82")
    assert scan(repo, since=None) == []

def test_kb_rename_and_wishlist_done(repo):
    rename(repo, "knowledge-base/a.md", "knowledge-base/topics/a.md", "scout: move a")
    edit(repo, "docs/wishlist/2026-11-11-x.md", "status: open", "status: done", "dreaming [18:41]: wishlist — x done")
    kinds = sorted(c.kind for c in scan(repo, since=None))
    assert kinds == ["kb-structure", "wishlist-done"]

def test_backfilled_flag_and_since(repo):
    old = commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — old", date="2026-06-02T01:00:00Z")
    commit(repo, "SKILL.md", "dreaming [01:00]: SKILL.md self-edit — new", date="2026-11-16T01:00:00Z")
    got = scan(repo, since=datetime(2026, 11, 1, tzinfo=UTC), backfilled=True)
    assert [c.summary for c in got] == ["SKILL.md self-edit — new"] and got[0].backfilled
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_changes_scan.py -v`
Expected: FAIL, `ImportError: cannot import name 'scan'`

- [ ] **Step 3: Implement `scan.py` as specified in Interfaces.** Use `scout.kb.git_io` if it already wraps `git log`; otherwise `subprocess.run(["git", "-C", str(vault), ...], check=False)`, and treat a non-repo or git failure as "no new changes".

- [ ] **Step 4: Run the tests and see them pass**

Run: `.venv/bin/python -m pytest tests/unit/test_changes_scan.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add plugin/engine/scout/changes/scan.py plugin/engine/tests/unit/test_changes_scan.py
git commit -m "feat(changes): git scanner turns self-change commits into ledger rows"
```

---

### Task 3: CLI (`changes scan`, `engagement record`, consent) and runner wiring

**Files:**
- Modify: `plugin/engine/scout/cli.py` (new `changes_app` and `engagement_app` typers, registered like `broker_app`)
- Create: `plugin/engine/scout/changes/consent.py`
- Modify: `plugin/templates/run-scout.sh.tmpl`, `plugin/templates/run-dreaming.sh.tmpl`, `plugin/templates/run-research.sh.tmpl`
- Test: `plugin/engine/tests/unit/test_cli_changes.py`, `plugin/engine/tests/unit/test_runner_changes_scan.py`

**Interfaces:**
- Consumes: `scan`, `append_signal`, `Signal`, `is_owner_session`, `owner_dir`
- Produces (`scout.changes.consent`):
  - `out_of_vault_answer(env: Mapping[str, str]) -> bool | None` (`None` = never asked)
  - `set_out_of_vault(answer: bool, env: Mapping[str, str]) -> None`: writes `owner_dir/consent.json` (`{"schema": 1, "out_of_vault": bool, "answered_at": iso}`), atomic and mode 0600 like `agent_runtimes.consent._write`. Raises `PermissionError` when not an owner session.
- Produces (CLI):
  - `scoutctl changes scan [--since 2d|ISO] [--backfill]`: prints `N new change(s)`, exit 0. `--backfill` with no `--since` means since `2026-06-01`.
  - `scoutctl engagement record KIND --surface S [--ref R] [--sentiment X] [--ts ISO]`: exit 0 on write; exit 2 with a one-line reason on `PermissionError` or a value outside its set.
  - `scoutctl engagement consent --out-of-vault yes|no` and `scoutctl engagement consent --show` (`yes` / `no` / `not asked`).
- Runner templates: after the session ends and before the success `run-outcome.sh record`, add
  `"$SCOUTCTL_BIN" changes scan --since 2d >> "$LOG_FILE" 2>&1 || true`

- [ ] **Step 1: Write the failing tests**

```python
def test_record_refuses_owner_kind_in_a_run(cli, vault, home, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    r = cli(["engagement", "record", "session-reliance", "--surface", "kb"])
    assert r.exit_code == 2 and not any(home.rglob("engagement.jsonl"))

def test_record_run_kind_in_a_run(cli, vault, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    r = cli(["engagement", "record", "dm-reply", "--surface", "slack-dm", "--ref", "1790330702.929009", "--sentiment", "positive"])
    assert r.exit_code == 0 and (vault / ".scout-logs/engagement.jsonl").read_text().count("\n") == 1

def test_record_rejects_unknown_surface(cli, vault):
    assert cli(["engagement", "record", "dm-reply", "--surface", "carrier-pigeon"]).exit_code == 2

def test_consent_owner_only(cli, home, monkeypatch):
    assert cli(["engagement", "consent", "--show"]).output.strip() == "not asked"
    assert cli(["engagement", "consent", "--out-of-vault", "yes"]).exit_code == 0
    monkeypatch.setenv("SCOUT_MODE", "briefing")
    assert cli(["engagement", "consent", "--out-of-vault", "no"]).exit_code == 2
    monkeypatch.delenv("SCOUT_MODE")
    assert cli(["engagement", "consent", "--show"]).output.strip() == "yes"

def test_changes_scan_cli(cli, git_vault):
    # one SKILL.md commit in git_vault -> output "1 new change(s)"; second call -> "0 new change(s)"

@pytest.mark.parametrize("tmpl", ["run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl"])
def test_runner_scans_after_session_before_outcome(tmpl):
    text = (TEMPLATES / tmpl).read_text()
    scan_at = text.index('"$SCOUTCTL_BIN" changes scan --since 2d')
    assert scan_at < text.rindex('run-outcome.sh" record')
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_cli_changes.py tests/unit/test_runner_changes_scan.py -v`
Expected: FAIL, `No such command 'changes'`

- [ ] **Step 3: Implement `consent.py`, the two typer apps and the three template lines as specified in Interfaces**

- [ ] **Step 4: Run them and see them pass, then run the bootstrap/render tests the templates feed**

Run: `.venv/bin/python -m pytest tests/unit/test_cli_changes.py tests/unit/test_runner_changes_scan.py tests/unit -k bootstrap -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add plugin/engine/scout/cli.py plugin/engine/scout/changes/consent.py plugin/templates/run-*.sh.tmpl plugin/engine/tests/unit/test_cli_changes.py plugin/engine/tests/unit/test_runner_changes_scan.py
git commit -m "feat(changes): scoutctl changes scan, engagement record and consent; runners scan after each session"
```

---

### Task 4: Reliance capture in the Stop hook

**Files:**
- Create: `plugin/engine/scout/changes/reliance.py`
- Modify: `plugin/engine/scout/hooks/session_tokens.py` (one call in `run()` after `ctx` is resolved, wrapped so nothing it raises escapes)
- Test: `plugin/engine/tests/unit/test_changes_reliance.py`

**Interfaces:**
- Consumes: `run_context.RunContext`, `is_owner_session`, `append_signal`, `Signal`, `out_of_vault_answer`, `paths.data_dir`, `paths.vault_containing`
- Produces:
  - `surfaces_used(transcript_path: Path, vault: Path, *, cwd: str) -> set[str]`. Members:
    - `vault-session` when `cwd` is inside `vault`
    - `kb` when a `Read`/`Grep`/`Glob`/`Edit` tool_use input path is under `vault/knowledge-base/`
    - `action-items` when such a path is under `vault/action-items/`
    - `skill` when a `Skill` tool_use names `scout:*`, or a user message contains `<command-name>/scout-` or starts with `/scout-`
    - `scoutctl` when a `Bash` tool_use command contains `scoutctl`

    Paths are resolved before comparing, so `knowledge_base/` symlink reads count as `kb`.
  - `record_reliance(payload: dict, transcript_path: Path | None, ctx: RunContext, env: Mapping[str, str], *, now: datetime) -> int`: returns rows written. Writes nothing when `ctx.mode` is set, when `not is_owner_session(env)`, or when there is no transcript. When `cwd` is outside the vault and `out_of_vault_answer(env)` is not `True`, writes nothing. Otherwise it writes one `session-reliance` signal per surface, with `session = sha256(session_id)[:16]`. The vault comes from `paths.data_dir()` (config / `SCOUT_DATA_DIR`), not from `cwd`.

- [ ] **Step 1: Write the failing tests** (transcripts are small JSONL fixtures built in the test)

```python
def test_in_vault_session_counts_kb_and_skill(vault, home):
    t = transcript(cwd=vault, tool_uses=[("Read", {"file_path": str(vault / "knowledge-base/people.md")})],
                   user_texts=["<command-name>/scout-work</command-name>"])
    assert surfaces_used(t, vault, cwd=str(vault)) == {"vault-session", "kb", "skill"}

def test_out_of_vault_needs_consent(vault, home, other_repo):
    t = transcript(cwd=other_repo, tool_uses=[("Read", {"file_path": str(vault / "action-items/action-items-2026-11-17.md")})])
    env = {"HOME": str(home)}
    assert record_reliance(payload(t, other_repo), t, owner_ctx(vault), env, now=NOW) == 0
    set_out_of_vault(True, env)
    assert record_reliance(payload(t, other_repo), t, owner_ctx(vault), env, now=NOW) == 1

def test_runs_never_count(vault, home):
    t = transcript(cwd=vault, tool_uses=[("Bash", {"command": "scoutctl kb delta"})])
    ctx = RunContext(vault, "dreaming", "prompt")  # prompt-named run with SCOUT_MODE unset
    assert record_reliance(payload(t, vault), t, ctx, {"HOME": str(home)}, now=NOW) == 0

def test_rows_hold_no_paths_or_text(vault, home):
    # after an in-vault session with a KB read, the owner engagement file contains neither
    # "knowledge-base" nor the session id in clear, nor any user text

def test_stop_hook_survives_garbage_transcript(vault, home, capsys):
    # session_tokens.run(stdin=payload pointing at a file of b"\x00not json") returns normally,
    # writes its usual row, and no exception escapes
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_changes_reliance.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'scout.changes.reliance'`

- [ ] **Step 3: Implement `reliance.py` and the guarded call in `session_tokens.run`**

- [ ] **Step 4: Run the new tests and the existing Stop-hook tests**

Run: `.venv/bin/python -m pytest tests/unit/test_changes_reliance.py tests/unit/test_hooks_session_tokens.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add plugin/engine/scout/changes/reliance.py plugin/engine/scout/hooks/session_tokens.py plugin/engine/tests/unit/test_changes_reliance.py
git commit -m "feat(changes): owner sessions that rely on Scout are counted at session end"
```

---

### Task 5: JSON contracts (`changes --json`, `engagement --json`)

**Files:**
- Create: `plugin/engine/scout/changes/report.py`
- Modify: `plugin/engine/scout/cli.py` (`--json` / `--since` / `--limit` options on the two apps' default commands)
- Create: `plugin/engine/tests/unit/test_changes_contract.py`, `plugin/engine/tests/fixtures/contract/changes.json`, `plugin/engine/tests/fixtures/contract/engagement.json` (both generated, never hand-edited)
- Modify: root `CLAUDE.md` (a regeneration line beside the run-ledger one)

**Interfaces:**
- Consumes: `read_changes`, `read_signals`, `run_ledger.parse_since` (reuse it, don't copy)
- Produces:
  - `changes_report(vault: Path, *, now: datetime, since: datetime, limit: int = 500) -> dict` returns `{"schema_version": 1, "generated_at", "since", "truncated", "skipped_rows", "changes": [Change.to_row() newest first], "counts": {kind: n}}`
  - `engagement_report(vault: Path, env: Mapping[str, str], *, now: datetime, since: datetime, tz: ZoneInfo) -> dict` returns `{"schema_version": 1, "generated_at", "since", "skipped_rows", "days": [{"date": "YYYY-MM-DD", "feedback": n, "reliance": n, "noise": n, "by_surface": {surface: n}}], "totals": {"feedback", "reliance", "noise"}}`. **Every calendar day in the range appears, zeros included** (spec §3: rates are per calendar day). Days are bucketed in `tz`, resolved the way the rest of the engine resolves the host zone. `feedback` = `dm-reply` + `dm-reaction` + `inline-comment` + `app-edit` (`approval` is not engagement). `reliance` = `session-reliance`. `noise` = `red-carried` only in Plan 1; Plan 2 adds unanswered `dm-sent`.
- CLI: `scoutctl changes --json [--since 30d] [--limit N]`, `scoutctl engagement --json [--since 30d]`

- [ ] **Step 1: Write the failing contract test**, modelled on `test_run_ledger_contract.py`: a deterministic vault and home at `NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)`, with one change of every kind (one with an inferred why, one backfilled) and one signal of every kind, plus a torn line in each file.

```python
def test_changes_contract_matches_canonical(...):
    doc = changes_report(vault, now=NOW, since=NOW - timedelta(days=30))
    assert doc["skipped_rows"] == 1 and set(doc["counts"]) == CHANGE_KINDS
    check_or_regen(CANONICAL_CHANGES, doc)

def test_engagement_contract_has_every_day(...):
    doc = engagement_report(vault, env, now=NOW, since=NOW - timedelta(days=14), tz=ZoneInfo("Europe/Prague"))
    assert len(doc["days"]) == 15 and doc["days"][3] == {"date": "2026-11-05", "feedback": 0, "reliance": 0, "noise": 0, "by_surface": {}}
    check_or_regen(CANONICAL_ENGAGEMENT, doc)

def test_reports_carry_no_absolute_paths(...):
    for doc in (changes_doc, engagement_doc):
        text = json.dumps(doc)
        assert str(vault) not in text and str(home) not in text
```

- [ ] **Step 2: Run it and see it fail**

Run: `.venv/bin/python -m pytest tests/unit/test_changes_contract.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'scout.changes.report'`

- [ ] **Step 3: Implement `report.py` and the CLI options; generate the fixtures**

Run: `env SCOUT_REGEN_CONTRACT=1 .venv/bin/python -m pytest tests/unit/test_changes_contract.py`

- [ ] **Step 4: Run without regen, then the whole engine unit suite**

Run: `.venv/bin/python -m pytest tests/unit/test_changes_contract.py -v && .venv/bin/python -m pytest tests/unit -q`
Expected: PASS. Full suite green.

- [ ] **Step 5: Commit**

```bash
git add plugin/engine/scout/changes/report.py plugin/engine/scout/cli.py plugin/engine/tests/unit/test_changes_contract.py plugin/engine/tests/fixtures/contract/changes.json plugin/engine/tests/fixtures/contract/engagement.json CLAUDE.md
git commit -m "feat(changes): scoutctl changes/engagement --json contracts for the app"
```

> The app's byte copy and the `contract.yml` comparison step land in Plan 3 together with the first client. Adding the CI step now would fail on "no client copy found".

---

### Task 6: CHANGELOG and the PR

**Files:**
- Modify: `plugin/CHANGELOG.md` (one Unreleased entry naming the three new commands, the owner store, the consent question, and that nothing reads the ledgers yet)

- [ ] **Step 1: Add the entry; run the full engine suite once more**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 2: Commit and open a draft PR on `Raven-Scout/Scout`** (draft PRs are within Scout's standing grant; merging is the owner's). Don't bump versions, and don't run either release script.

```bash
git add plugin/CHANGELOG.md
git commit -m "docs(changelog): changes map plan 1 — ledgers and capture"
```
