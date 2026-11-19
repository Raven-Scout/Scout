# Changes Map — Plan 2: Verdicts, Owner Decisions and Run Phases

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Scout self-change gets a before/after test and a verdict. The owner can approve, reject, undo and mark verdicts wrong. Scheduled runs record the Slack, stale-🔴 and recurrence signals the verdicts need, and they carry out the owner's decisions.

**Architecture:** Plan 1's `scout.changes` package gains a pure verdict engine (`verdicts.py`), owner settings and governance files (`settings.py`, `governance.py`), and run-side actions (`actions.py`: what is due, rollback, apply a governance patch). Verdicts are recomputed from the ledgers on every read, with no derived state file a run could forge. The phase files tell runs when to record signals and when to act; the materializer records stale 🔴s itself.

**Tech Stack:** Python 3 (engine, typer CLI, pytest), Markdown phase fragments assembled into the vault brains.

**Spec:** `docs/superpowers/specs/2026-11-17-changes-map-design.md`. Read §3, §6.1, §6.2, §6.4, §7, §7a, §8 and §11 before your task. Plan 1 (`docs/superpowers/plans/2026-11-17-changes-map-plan-1-engine-ledger.md`) built the ledgers this plan reads; its code is on `main` under `plugin/engine/scout/changes/`.

## The five plans

| Plan | Delivers | Status |
|---|---|---|
| 1 | Ledgers, git scanner, `engagement record`, reliance capture + consent, JSON contracts | merged (Raven-Scout/Scout#679) |
| **2 (this)** | Verdict engine + calibration (§7, §7a); owner decisions; governance path (§8); run phases: Slack 7-day re-read, `dm-sent`, `red-carried`, recurrences, owner-away days, test-loop step, Step 1e removal, commit trailers | — |
| 3 | App "Changes" tab v1, remove Proposals + Wishlist tabs, contract copies, **the install-time consent question (§6.3)**, **`inline-comment` and `app-edit` rows (§6.4)**, engine draft PRs in the "Needs your OK" strip | 1, 2 |
| 4 | `scoutctl changes digest`, weekly digest DM, `/scout-status` section | 2 |
| 5 | Feedback backfill from the DM archive; opt-in transcript backfill | 1 |

## Decisions this plan makes (the spec leaves them open or Plan 1 parked them)

- **Verdicts are computed on read, not stored.** The spec's `change-tests.json` would sit in run-writable `.scout-logs/`, where a run could write its own verdicts. Every input is in the append-only ledgers, so `scoutctl changes --json` recomputes them each time.
- **Change rows are re-derived from git on read (parked M3).** `changes.jsonl` is run-writable, so the report keeps a row only when its `id` is `chg-` + the first 12 hex characters of its commit, and replaces the row's fields with what the scanner derives from that commit now. A row for a commit that isn't a self-change is dropped and counted in `skipped_rows`.
- **The `auto_rollback` switch and the calibration date live in the owner store** (`settings.json`), not in `scout-config.yaml`. A run can write `scout-config.yaml` and could switch rollback on for itself; this follows Plan 1's consent ruling.
- **Pending governance changes come from the `governance-pending/` folder**, not from ledger rows. The folder is the source of truth (one `<slug>.md` per item), and `changes --json` lists it under `needs_ok`. This stands in for the spec's "change row with `state: awaiting-ok`".
- **New signal kinds.** `recurrence` (a mistake pattern recurred, recorded by feedback processing), `owner-away` (an away day from the calendar, recorded by the briefing) and `verdict-wrong` (owner-only; counts as feedback). Owner decisions reuse Plan 1's `approval` kind, with refs `approve:<id>`, `reject:<id>` and `undo:<id>`.
- **DM refs join replies to sends.** A `dm-sent` row's ref is the bot message's Slack ts. A reply's ref is `<bot ts>/<reply ts>`, and a reaction's is `<bot ts>/r:<emoji name>`. A top-level owner message in the DM counts as a reply to the newest bot message sent in the 48 h before it.
- **"Untouched" for a carried 🔴 uses `scout.action_items.stale`'s rule**: the item's block text hasn't changed, ignoring date-only edits. A run rewriting the text also counts as a touch, so noise can be undercounted, never overcounted.
- **Left out on purpose:**
  - `scoutctl engagement verify` (spec §6.1) is replaced for change rows by the git re-derivation above. Checking Slack refs against Slack needs the Slack connector, which the engine doesn't have, so a sampled Slack check waits for a later plan.
  - Telegram sends are not recorded. They are excluded from the 48 h rule until replies are readable (§6.4), so no number would use them.
  - The `phase` change kind stays unused for vault scans (parked M6). A vault has no phase files; a phase change shows up in the vault as a brain edit, and the root-level `BRAIN_FILES` match is correct.
- **What gets tested.**
  - **Tested:** changes from the run lanes `briefing`, `consolidation`, `dreaming`, `research` and `work`.
  - **Not tested:** `interactive` and `update` lanes, `rollback` and `upgrade` kinds, and backfilled rows. The spec says interactive changes are not tested and backfilled ones get no verdict. An upgrade is engine code Scout can't undo, and a rollback is the outcome of a test.

## Global Constraints

- Contracts stay `schema_version: 1`. Every addition is a new key; a client ignores keys it doesn't know. Row kinds and surfaces are validated against the fixed sets in `scout/changes/models.py`.
- No content in a row: no prompt or message text, no path outside the vault, no project name. Refs are ids only: Slack ts, `pattern-N@…`, a hash, a date or a change id.
- Owner-sourced kinds (`session-reliance`, `app-edit`, `inline-comment`, `approval`, `verdict-wrong`) and `settings.json` are written only to `~/.local/state/scout/scout-change-ledger-owner/`, and only when `store.is_owner_caller(env, vault)` is true. Otherwise: exit 2, nothing written. Runs may read the owner store; the sandbox denies only writes there.
- Verdict rules (spec §7), all rates per calendar day in the host timezone (`scout.config.resolve_timezone`):
  - **Windows.** Baseline is the 14 days before the change. The review window is `review_days` days after it (default 14).
  - **`can't tell`:** `owner-away` days exceed half of either window. It is never inferred from low engagement.
  - **`hurt`:** noise up ≥ 25 % (after ≥ 1.25 × before and after > before) and engagement not up (after ≤ before); or targeted recurrences after > before; or a negative `dm-reply` names the change.
  - **`helped`:** targeted recurrences fell from > 0 to 0; or engagement after ≥ 1.15 × before and after > before, with noise after ≤ before.
  - **Otherwise:** `no clear effect`.
  - **Overrides:** `rolled back` overrides every verdict.
- Engagement = feedback (`dm-reply`, `dm-reaction`, `inline-comment`, `app-edit`, `verdict-wrong`) + reliance (`session-reliance`). Noise = `red-carried` + each `slack-dm` `dm-sent` with no reply or reaction within 48 h.
- `auto_rollback` is `off | targeted | one-at-a-time`, default `off`. Automatic rollback acts only when the mode isn't `off` **and** calibration has ended (`calibrated_on` set by the owner).
- Never roll back these:
  - a governance change;
  - a change from an untested lane or kind;
  - a change whose files a later commit has touched;
  - a root commit.
  Report each one as blocked instead.
- Engine code never runs `git add`, `commit`, `reset` or `checkout`. Use read-only git calls, plus `git apply` on the working tree only. Every git subprocess sets `GIT_OPTIONAL_LOCKS=0` and a timeout, and decodes with `errors="replace"`. Runs commit only through `scripts/git-safe-commit.sh`.
- At most one engagement-targeted change (its `targets` include `engagement`) may be under test at a time. `changes due` reports the running count; the phase forbids starting another.
- Readers and hooks never raise. The materializer never fails a run over a changes-map error.
- Phase text is written for a cold reader: every id gets a plain-words gloss.
- Never run `release-app.sh`, `release-plugin.sh` or `scripts/release.sh`.
- Tests run from `plugin/engine`: `.venv/bin/python -m pytest <path> -v`. `tests/conftest.py` scrubs `SCOUT_*` variables before each test.

## Review Focus

1. **DM timing.** A reply 49 h after the send still leaves that DM as noise. A DM sent 30 h ago is pending, not yet noise. Pinned in Task 3.
2. **An owner who stops using Scout, without being away.** Their DMs go unanswered, noise rises, and the verdict is `hurt`, never `can't tell`. Pinned in Task 4.
3. **A rollback of a change that renamed or added files, or whose files changed later.**
   - A rename restores the old path and deletes the new one.
   - An added file is deleted.
   - A file touched by a later commit blocks the whole rollback; nothing is partly restored.
   Pinned in Task 6.
4. **A scheduled run tries to approve, reject, undo, mark a verdict wrong, change `auto-rollback` or end calibration.** Exit 2, nothing written. A row a run seeded for a commit that isn't a self-change never reaches the report. Pinned in Tasks 2 and 5.
5. **The 7-day re-read records the same reply in two dreaming runs.** One row; the second call prints `already recorded` and exits 0. Pinned in Task 1.

---

### Task 1: Signal kinds, dedupe and record CLI hardening

**Files:**
- Modify: `plugin/engine/scout/changes/models.py`, `plugin/engine/scout/changes/store.py`, `plugin/engine/scout/changes/reliance.py`, `plugin/engine/scout/changes/vault.py`, `plugin/engine/scout/cli.py` (`cli_engagement_record`)
- Test: `plugin/engine/tests/unit/test_changes_store.py`, `plugin/engine/tests/unit/test_cli_changes.py`, `plugin/engine/tests/unit/test_changes_reliance.py`

**Interfaces:**
- Produces (`models`):
  - **`RUN_KINDS`** = `{"dm-reply","dm-reaction","dm-sent","red-carried","recurrence","owner-away"}`.
  - **`OWNER_KINDS`** adds `"verdict-wrong"`.
  - **`SURFACES`** adds `"mistake-audit"` and `"calendar"`.
  - **`WHY_TYPES`** adds `"verdict"`.
  - **`CHANGE_ID_RE`** = `re.compile(r"^chg-[0-9a-f]{12}$")`.
  - **`REF_REQUIRED`** = `RUN_KINDS | {"approval", "verdict-wrong"}`.
  - **`Signal`** gains `change: str | None = None`. `to_row` writes `"change"`. `from_row` accepts it when it is absent or `None`, and raises `ValueError` unless it matches `CHANGE_ID_RE`.
  - **`Change`** gains `reverts: str | None = None`, validated the same way (Task 2 fills it).
- Produces (`store`):
  - **`has_signal(vault: Path, env: Mapping[str, str], kind: str, ref: str) -> bool`** reads the store that `kind` is written to. It never raises.
  - **`append_signal_once(signal: Signal, *, vault: Path, env: Mapping[str, str]) -> bool`** returns False and writes nothing when `signal.ref` is set and `has_signal(...)` is true for its kind and ref.
- Produces (CLI): `scoutctl engagement record`:
  - **`--change chg-…`.** The option is accepted only with `dm-reply`.
  - **`--ts`.** It also accepts a Slack ts (`^\d{9,11}(\.\d+)?$`, epoch seconds, read as UTC).
  - **Refs.** `REF_REQUIRED` kinds without `--ref` exit 2. All writes go through `append_signal_once`; a duplicate prints `already recorded` and exits 0.

- [ ] **Step 1: Write the failing tests**

```python
def test_signal_change_field_round_trips_and_rejects_bad_ids(): ...
    # Signal(..., kind="dm-reply", change="chg-0123456789ab").to_row()["change"] == "chg-0123456789ab"
    # Signal.from_row({... "change": "chg-xyz"}) raises ValueError

def test_append_signal_once_skips_a_duplicate_kind_and_ref(tmp_path): ...
    # first call True, second False, read_signals -> one row

def test_record_twice_prints_already_recorded(cli_vault): ...
    # `engagement record dm-reply --surface slack-dm --ref 1791624082.460129/1791625000.000100 --ts 1791625000.000100`
    # twice -> exit 0 both, second stdout "already recorded", one row; row ts == 2026-10-10T... UTC from the epoch

def test_record_refuses_a_run_kind_without_ref(cli_vault): ...      # dm-sent with no --ref -> exit 2
def test_record_refuses_change_on_other_kinds(cli_vault): ...       # dm-sent --change chg-… -> exit 2
def test_verdict_wrong_is_owner_only(cli_vault, monkeypatch): ...   # SCOUT_MODE=dreaming -> exit 2, no file

def test_empty_session_id_records_no_reliance(tmp_path): ...        # parked M8
def test_deeply_nested_transcript_line_is_skipped(tmp_path): ...    # a line of 100_000 nested [ -> surfaces from the other lines still found
def test_record_with_no_home_exits_2(cli_vault, monkeypatch): ...   # HOME unset and SCOUT_DATA_DIR unset -> exit 2, no traceback
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_changes_store.py tests/unit/test_cli_changes.py tests/unit/test_changes_reliance.py -v`
Expected: the new tests FAIL (unknown kind, no `change` field, no dedupe).

- [ ] **Step 3: Implement**
  - **Models and store:** the model and store changes above.
  - **`reliance.record_reliance`:** returns 0 when `payload["session_id"]` is empty.
  - **`reliance._rows`:** catches `RecursionError` per line, beside `ValueError`.
  - **`vault.installed_vault`:** raises `ValueError("no home directory")` instead of letting `RuntimeError` escape.
  - **CLI:** every CLI caller of `installed_vault` turns that into exit 2.

- [ ] **Step 4: Run the same command; expect PASS.** Then run `tests/unit/test_changes_*.py tests/unit/test_cli_changes.py`, all of which must pass.

- [ ] **Step 5: Commit**

```bash
git add plugin/engine/scout/changes plugin/engine/scout/cli.py plugin/engine/tests/unit
git commit -m "feat(changes): new signal kinds, ref dedupe, Slack-ts times"
```

---

### Task 2: Change rows that can't be forged, and rollback rows that point at what they undo

**Files:**
- Modify: `plugin/engine/scout/changes/scan.py`, `plugin/engine/scout/changes/report.py`, `plugin/engine/scout/changes/models.py`
- Test: `plugin/engine/tests/unit/test_changes_scan.py`, `plugin/engine/tests/unit/test_changes_contract.py`, `plugin/engine/tests/fixtures/contract/changes.json`

**Interfaces:**
- Consumes: `Change.reverts`, `CHANGE_ID_RE`, and why type `verdict` from Task 1.
- Produces (`scan`):
  - `parse_trailers` also reads `Scout-Change-Reverts:`.
  - `classify` returns `"rollback"` when the trailers carry `reverts` or the subject starts with `Revert `, before any path rule.
  - `reverts` comes from the trailer value when it matches `CHANGE_ID_RE`, else from a body line `This reverts commit <40 hex>` as `chg-<first 12>`, else `None`.
  - `_why_from_trailer("verdict:chg-…")` gives `Why("verdict", …)`.
  - `derive(vault: Path, shas: Sequence[str]) -> dict[str, Change | None] | None` runs one `git log --no-walk=unsorted --no-merges -M --name-status` over the shas, with the scanner's format. Each sha maps to the row the scanner would write now (or `None` when the commit is not a self-change or is missing). The whole call returns `None` when git can't be read.
  - `_record` and `derive` share one `_change_from_commit(...) -> Change | None`.
- Produces (`report.changes_report`):
  - Every unique ledger id is re-derived.
  - A row is dropped (and counted in `skipped_rows`) when its id isn't `chg-` + `commit[:12]` or `derive` gives `None`.
  - The derived row is used, keeping the ledger row's `backfilled`.
  - A new top-level key `"verified": bool` is false only when `derive` returned `None`; the ledger rows are then used as they are.

- [ ] **Step 1: Write the failing tests** (in `test_changes_scan.py`, using its existing git-repo fixture)

```python
def test_trailer_reverts_makes_a_rollback_row(repo): ...
    # commit touching SKILL.md with "Scout-Change-Reverts: chg-0123456789ab" -> kind "rollback", reverts "chg-0123456789ab"
def test_git_revert_body_sets_reverts(repo): ...
    # subject 'Revert "x"', body "This reverts commit <sha>." -> reverts == "chg-" + sha[:12]
def test_verdict_why_trailer(repo): ...                      # "Scout-Change-Why: verdict:chg-…" -> why.type == "verdict"
def test_report_drops_a_seeded_row_for_a_non_change_commit(repo): ...
    # append_change(vault, Change(id="chg-"+trading_sha[:12], commit=trading_sha, kind="brain-edit", ...))
    # changes_report(...)["changes"] == [] and ["skipped_rows"] == 1 and ["verified"] is True
def test_report_overrides_a_seeded_rows_fields_with_gits(repo): ...
    # seeded row for a real brain-edit commit claiming governance=True, summary "x" -> report shows the commit's own values
def test_report_without_git_marks_unverified(tmp_path): ...  # vault that is not a repo -> "verified": False, rows as written
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_changes_scan.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement the interfaces above.** Fix parked M7 while regenerating the contract fixture. The builder in `test_changes_contract.py` must use coherent data:
  - a `brain-edit` on `SKILL.md` with `why` `pattern-288` and `targets: ["pattern-288"]`;
  - a `driver` on `scripts/check-clock.py`;
  - a `rollback` with `reverts` set.
  Regenerate `tests/fixtures/contract/changes.json` with the test's existing `--update` path (or whatever the file's docstring names).

- [ ] **Step 4: Run** `tests/unit/test_changes_scan.py tests/unit/test_changes_contract.py`. Expect PASS.

- [ ] **Step 5: Commit** — `feat(changes): re-derive change rows from git on read; rollback rows name what they undo`

---

### Task 3: Unanswered DMs count as noise

**Files:**
- Modify: `plugin/engine/scout/changes/report.py`
- Test: `plugin/engine/tests/unit/test_changes_contract.py` (engagement half), `plugin/engine/tests/unit/test_cli_changes.py`, `plugin/engine/tests/fixtures/contract/engagement.json`

**Interfaces:**
- Consumes: the ref format from "Decisions": `dm-sent` ref = `<bot ts>`, reply = `<bot ts>/<reply ts>`, reaction = `<bot ts>/r:<emoji>`.
- Produces:
  - `DM_ANSWER_WINDOW = timedelta(hours=48)`.
  - `FEEDBACK_KINDS` adds `"verdict-wrong"`.
  - `daily_counts(signals: Sequence[Signal], *, since: datetime, now: datetime, tz: ZoneInfo) -> dict[str, dict]` gives the per-day buckets `engagement_report` builds today (same shape, `by_surface` included). It is factored out so the verdict engine (Task 4) counts exactly what the report shows.
  - A `slack-dm` `dm-sent` row (with a ref) is noise on the local day it was sent once `now - sent.ts >= DM_ANSWER_WINDOW`, unless a `dm-reply` or `dm-reaction` row has a ref starting with `f"{sent.ref}/"` and a ts in `[sent.ts, sent.ts + 48 h]`.
  - `engagement_report` adds a top-level `"pending": {"dm_sent": n}`: DMs younger than 48 h with no answer yet.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_unanswered_dm_is_noise_after_48h(): ...      # sent 50 h before now -> its day noise == 1, by_surface["noise"] == {"slack-dm": 1}
def test_a_reply_at_49h_leaves_it_noise(): ...           # reply ts = sent + 49 h -> noise 1, feedback 1 (on the reply's day)
def test_a_reaction_within_48h_answers_it(): ...         # ref "<bot>/r:+1" at sent + 2 h -> noise 0
def test_a_young_dm_is_pending_not_noise(): ...          # sent 30 h before now -> noise 0, pending.dm_sent == 1
def test_a_reply_to_another_dm_does_not_answer_it(): ... # reply ref "<other bot ts>/…" -> noise 1
def test_verdict_wrong_counts_as_feedback(): ...
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_changes_contract.py -v -k "dm or verdict_wrong"`. Expected: FAIL.

- [ ] **Step 3: Implement.** Factor `daily_counts` out first, then add the 48 h rule. Then regenerate `tests/fixtures/contract/engagement.json`; the builder must include one answered DM, one unanswered DM and one pending DM (M7).

- [ ] **Step 4: Run** the `test_changes_*` and `test_cli_changes` files. Expect PASS.

- [ ] **Step 5: Commit** — `feat(changes): a DM with no reply or reaction in 48 h counts as noise`

---

### Task 4: The verdict engine

**Files:**
- Create: `plugin/engine/scout/changes/settings.py`, `plugin/engine/scout/changes/verdicts.py`
- Modify: `plugin/engine/scout/changes/report.py` (`changes_report` gets `env` and `tz` and attaches `test`), `plugin/engine/scout/cli.py` (`cli_changes` passes them)
- Test: `plugin/engine/tests/unit/test_changes_verdicts.py`, `plugin/engine/tests/unit/test_changes_contract.py`, fixture `changes.json`

**Interfaces:**
- Consumes: `daily_counts` (Task 3); `recurrence`, `owner-away` and `verdict-wrong` kinds and `Signal.change` (Task 1); `Change.reverts` and re-derived rows (Task 2).
- Produces (`settings`):
  - `AUTO_MODES = ("off", "targeted", "one-at-a-time")`.
  - `@dataclass(frozen=True) Settings(auto_rollback: str = "off", review_days: int = 14, calibrated_on: date | None = None)`.
  - `read_settings(env) -> Settings` reads `owner_dir(env)/settings.json` (`{"schema": 1, ...}`). Anything missing or malformed falls back to the defaults, and it never raises. `review_days` is clamped to 7–60.
  - `write_settings(settings: Settings, env) -> None` writes atomically with mode 0600, reusing `reliance._atomic_write`. Move that function to `store.py` as `atomic_write_json` and import it in both places.
- Produces (`verdicts`):
  - Constants: `BASELINE_DAYS = 14`, `NOISE_UP = 1.25`, `ENGAGEMENT_UP = 1.15`, `TESTED_LANES = frozenset({"briefing","consolidation","dreaming","research","work"})`, `UNTESTED_KINDS = frozenset({"rollback","upgrade"})`.
  - `@dataclass(frozen=True) Window(days: int, engagement: float, noise: float, recurrences: Mapping[str, int], away_days: int)`. `engagement` and `noise` are per-calendar-day means.
  - `window(signals, *, start: date, days: int, now: datetime, tz: ZoneInfo, targets: Sequence[str]) -> Window`. Recurrences count `recurrence` rows whose ref, before the first `@`, is in `targets`. Away days are the distinct `owner-away` refs (ISO dates) that fall in the window.
  - `judge(before: Window, after: Window, *, targets: Sequence[str], named_negative: bool) -> tuple[str, tuple[str, ...]]` returns the verdict and its reasons. The reasons are a subset of `{"away","noise-up","recurrence-up","named-negative","recurrence-zero","engagement-up"}`. The order is `can't tell`, then `hurt`, then `helped`, then `no clear effect`. Only `pattern-*` targets count for recurrences.
  - `TARGETED_REASONS = frozenset({"recurrence-up", "named-negative"})`.
  - `evaluate(changes: Sequence[Change], signals: Sequence[Signal], *, now: datetime, tz: ZoneInfo, settings: Settings) -> dict[str, dict]` maps a change id to its `test` object. The object's fields:
    - `state`: `not-tested`, `testing` or `done`.
    - `review_on`: the ISO date of `ts + review_days`, in `tz`.
    - `baseline` and `after`: Window dicts. `after` is `None` while testing.
    - `verdict` and `reasons`.
    - `suspect`: true when the verdict is `hurt`.
    - `rolled_back_by`: the id of a rollback change whose `reverts` is this id. It sets `verdict` to `"rolled back"` and `suspect` to false.
    - `overlap`: the other tested ids within `review_days` of this one, sorted.
    - `calibrating`: `settings.calibrated_on is None`.
    - `would_have_done`: `{"targeted": hurt with a targeted reason, "one_at_a_time": hurt and no newer member of its overlap group is suspect}`.
    - `marked_wrong`: true when there is a `verdict-wrong` row with this id as ref.
    - `engagement_targeted`: true when `"engagement"` is in `targets`.
  - `named_negative` is true when there is a `dm-reply` with `sentiment == "negative"` and `change == id`, timed in the review window.
- Produces (`report`): each entry in `changes_report(...)["changes"]` gains `"test"`, and the document gains top-level `"calibrating"` and `"auto_rollback"`.

- [ ] **Step 1: Write the failing tests** (`test_changes_verdicts.py`; build signals in memory, no files)

```python
def test_cant_tell_when_away_more_than_half_a_window(): ...     # 8 owner-away days in the review window -> "can't tell", ("away",)
def test_low_engagement_alone_is_never_cant_tell(): ...
def test_owner_who_stopped_replying_is_hurt(): ...
    # baseline: 3 dm-sent/day, all answered; after: 3/day, none answered, reliance -> 0, no away days
    # -> "hurt" with "noise-up"
def test_zero_noise_baseline_then_noise_is_up(): ...            # before.noise 0, after 0.5, engagement flat -> hurt
def test_flat_everything_is_no_clear_effect(): ...              # all zeros both windows -> "no clear effect", not helped
def test_targeted_recurrence_up_is_hurt_and_targeted(): ...     # would_have_done["targeted"] True
def test_recurrences_falling_to_zero_is_helped(): ...
def test_engagement_up_15_with_noise_flat_is_helped(): ...      # 2.0 -> 2.3 per day, noise equal -> helped; 2.0 -> 2.29 -> no clear effect
def test_named_negative_reply_is_hurt(): ...
def test_rollback_overrides_and_clears_suspect(): ...
def test_untested_lanes_kinds_and_backfill(): ...               # interactive, update, rollback, upgrade, backfilled -> state "not-tested", verdict None
def test_testing_until_review_day(): ...                        # now = ts + 13 days -> "testing", after None
def test_overlap_and_one_at_a_time_picks_the_newest(): ...      # two hurt changes 3 days apart -> only the newer has one_at_a_time True
def test_settings_bad_file_reads_defaults(tmp_path): ...        # garbage settings.json -> Settings()
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_changes_verdicts.py -v`. Expected: FAIL (no module).

- [ ] **Step 3: Implement `settings.py`, then `verdicts.py`, then wire `changes_report`.** Window days are local calendar dates: a window starting on day D covers D through D + days − 1. The baseline starts at `ts.date(tz) − 14`, and the review window starts on the change's own local date. Regenerate the `changes.json` fixture: the coherent rows from Task 2, one `testing`, one `done` with a verdict, one `not-tested`.

- [ ] **Step 4: Run** `tests/unit/test_changes_*.py tests/unit/test_cli_changes.py`. Expect PASS.

- [ ] **Step 5: Commit** — `feat(changes): before/after verdicts for every run-made change`

---

### Task 5: Owner decisions and the governance folder

**Files:**
- Create: `plugin/engine/scout/changes/governance.py`
- Modify: `plugin/engine/scout/cli.py`, `plugin/engine/scout/changes/report.py` (`needs_ok`)
- Test: `plugin/engine/tests/unit/test_changes_governance.py`, `plugin/engine/tests/unit/test_cli_changes.py`

**Interfaces:**
- Consumes: `Settings`/`read_settings`/`write_settings` (Task 4); `approval` and `verdict-wrong` with `append_signal_once` (Task 1); `store.is_owner_caller`.
- Produces (`governance`):
  - `PENDING_DIR = "governance-pending"`.
  - `@dataclass(frozen=True) Pending(id: str, slug: str, summary: str, why: str, targets: tuple[str, ...], path: str, diff: str)`. `id` is `"gov-" + slug`, and `path` is vault-relative.
  - `read_pending(vault: Path) -> tuple[list[Pending], int]`:
    - **What it reads:** each `governance-pending/<slug>.md` directly in the folder; `applied/` is skipped. A file has YAML frontmatter (`summary` ≤ 120 chars, `why`, optional `targets` list) and exactly one fenced `diff` block.
    - **Bad files:** files that don't fit are counted, not raised.
    - **Slugs:** `^[a-z0-9][a-z0-9-]{0,60}$`.
  - `decision(signals: Sequence[Signal], target_id: str) -> str` returns `"approved"`, `"rejected"` or `"awaiting-ok"`. The newest `approval` row with ref `approve:<id>` or `reject:<id>` wins.
  - `undo_requested(signals, change_id: str) -> bool`.
- Produces (`report`): `changes_report` adds `"needs_ok": [{"id","summary","why","targets","path","state"}]` for every readable pending file, plus `"needs_ok_skipped": n`.
- Produces (CLI; every command is owner-only, and a refusal is exit 2 with nothing written):
  - **`scoutctl changes approve <gov-id>`** and **`reject <gov-id>`**: the id must be in `read_pending`.
  - **`scoutctl changes undo <chg-id>`**: the change must exist in `changes_report` with `test.state != "not-tested"`, and it must not be a governance change.
  - **`scoutctl changes wrong <chg-id> [--surface scoutctl|app]`**: records `verdict-wrong`.
  - **`scoutctl changes auto-rollback off|targeted|one-at-a-time`**.
  - **`scoutctl changes calibrated [--reset]`**: sets `calibrated_on` to today in the host timezone, or clears it.
  - The `approval` surface is `scoutctl`, or `app` when `--surface app` is passed (Plan 3's app passes it).

- [ ] **Step 1: Write the failing tests**

```python
def test_read_pending_parses_a_good_file_and_counts_a_bad_one(tmp_path): ...
def test_applied_folder_is_not_pending(tmp_path): ...
def test_newest_decision_wins(): ...                                   # approve then reject -> "rejected"
def test_approve_reject_undo_wrong_are_owner_only(cli_vault, monkeypatch): ...
    # SCOUT_MODE=dreaming: each of approve/reject/undo/wrong/auto-rollback/calibrated -> exit 2;
    # owner dir has no engagement.jsonl and no settings.json afterwards
def test_approve_unknown_id_exits_2(cli_vault): ...
def test_undo_refuses_interactive_and_governance_changes(cli_vault, repo_commits): ...
def test_auto_rollback_writes_settings(cli_vault): ...                 # then read_settings(...).auto_rollback == "targeted"
def test_changes_json_lists_needs_ok_with_state(cli_vault): ...
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_changes_governance.py tests/unit/test_cli_changes.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement.** Frontmatter is parsed with the engine's safe YAML loader (`scout.yaml_safe`). A diff block that holds a path outside the vault (`../`, absolute) makes the file unreadable (counted).

- [ ] **Step 4: Run** the same files plus `test_changes_contract.py`, regenerating the `changes.json` fixture with one `needs_ok` entry. Expect PASS.

- [ ] **Step 5: Commit** — `feat(changes): owner approve/reject/undo/wrong and the governance-pending folder`

---

### Task 6: What a run must do, and doing it

**Files:**
- Create: `plugin/engine/scout/changes/actions.py`
- Modify: `plugin/engine/scout/cli.py`
- Test: `plugin/engine/tests/unit/test_changes_actions.py`, `plugin/engine/tests/unit/test_cli_changes.py`

**Interfaces:**
- Consumes: `evaluate` and `Settings` (Task 4); `read_pending`, `decision` and `undo_requested` (Task 5); `derive` (Task 2).
- Produces (`actions`):
  - `ANNOUNCED_NAME = "changes-announced.json"` in `.scout-logs/` (`{"schema": 1, "ids": [...]}`). It is run-writable on purpose: forging it only hides a wrap line, and the map still shows the verdict.
  - `due(vault: Path, env, *, now: datetime, tz: ZoneInfo) -> dict` returns `{"schema_version": 1, "rollbacks": [...], "blocked": [...], "apply": [...], "discard": [...], "suspect_unannounced": [...], "engagement_tests_running": n, "calibration_report_due": bool}`:
    - **`rollbacks`** are `{"id", "summary", "reason"}`. `reason` is `owner-undo` for each `undo:` request with no rollback row reverting it. It is `auto-targeted` or `auto-one-at-a-time` for each `would_have_done` match when `settings.auto_rollback` isn't `off` and `calibrating` is false.
    - **`blocked`** are candidates that fail `rollback_problem`, as `{"id", "summary", "why_blocked"}`.
    - **`apply`** and **`discard`** are pending ids whose decision is `approved` or `rejected`.
    - **`suspect_unannounced`** are suspect changes not in the announced file, as `{"id", "summary", "reasons"}`.
    - **`engagement_tests_running`** counts changes in `testing` state with `engagement_targeted`.
    - **`calibration_report_due`** is true when calibrating, today ≥ the first `done` verdict's `review_on` + 28 days, and `docs/changes-calibration-report.md` doesn't exist.
  - `rollback_problem(vault: Path, change: Change, test: dict) -> str | None` returns `None` when the change can be rolled back, else one of these:
    - `"governance"`;
    - `"not-a-run-change"`, when `test.state == "not-tested"`;
    - `"root-commit"`;
    - `"edited-since"`, when `git log --format=%H <commit>..HEAD -- <every path in git diff --name-status <commit>^ <commit>, both sides of a rename>` is non-empty;
    - `"git-unreadable"`.
  - `rollback(vault: Path, env, change_id: str, *, now: datetime, tz: ZoneInfo) -> list[str]`:
    - **Refusals:** it raises `ActionRefused(reason)` unless the id is in `due(...)["rollbacks"]`.
    - **Restoring:** each path in the change's `<commit>^..<commit>` diff gets its content at `<commit>^` (via `git show <commit>^:<path>`, bytes), or is deleted when `<commit>^` doesn't have it.
    - **Result:** it returns the sorted vault-relative paths, both sides of a rename included, for the run to pass to `git-safe-commit.sh`.
  - `apply_governance(vault: Path, env, gov_id: str) -> list[str]`:
    - **Refusals:** it raises `ActionRefused` unless the decision is `approved`.
    - **Patching:** it writes the diff to `.scout-cache/<slug>.diff` and runs `git apply --whitespace=nowarn -- <that file>`, working tree only.
    - **Bookkeeping:** it moves the md to `governance-pending/applied/<slug>.md` and deletes the temp diff.
    - **Result:** it returns the patched paths and both md paths. A failed `git apply` leaves the md in place and raises `ActionRefused("patch does not apply")`.
  - `discard_governance(vault: Path, env, gov_id: str) -> list[str]` raises unless the decision is `rejected`, then deletes the md and returns its path.
  - `mark_announced(vault: Path, ids: Sequence[str]) -> None`.
- Produces (CLI; open to runs and the owner):
  - `scoutctl changes due --json`.
  - `scoutctl changes rollback <chg-id>`, `apply <gov-id>` and `discard <gov-id>`, each printing one path per line. `ActionRefused` gives exit 2 with the reason on stderr.
  - `scoutctl changes announced <id>…`.

- [ ] **Step 1: Write the failing tests** (`test_changes_actions.py`, real git repos in `tmp_path`, owner rows via `append_signal` with an owner env)

```python
def test_owner_undo_is_due_once_then_not_after_the_rollback_commit(repo): ...
def test_auto_modes_need_calibration_to_act(repo): ...        # auto_rollback "targeted", calibrated_on None -> rollbacks == []
def test_rollback_restores_modified_deletes_added_and_unrenames(repo): ...
    # change commit: modify SKILL.md, add scripts/new.py, git mv knowledge-base/a.md knowledge-base/b/a.md
    # rollback(...) -> SKILL.md == parent content, scripts/new.py gone, knowledge-base/a.md back, knowledge-base/b/a.md gone;
    # returned paths include all four
def test_a_later_edit_blocks_and_nothing_is_written(repo): ... # later commit touches SKILL.md -> due blocked "edited-since"; rollback raises; tree unchanged
def test_governance_change_is_blocked(repo): ...
def test_apply_governance_patches_and_moves_the_file(repo): ...
def test_apply_without_approval_refuses(repo): ...
def test_failed_patch_leaves_the_pending_file(repo): ...
def test_engagement_tests_running_counts_only_testing_engagement_changes(repo): ...
def test_suspects_are_announced_once(repo): ...                # after mark_announced, suspect_unannounced == []
def test_calibration_report_due(repo): ...
def test_git_calls_set_optional_locks_off(monkeypatch, repo): ...  # every subprocess env has GIT_OPTIONAL_LOCKS == "0"
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_changes_actions.py -v`. Expected: FAIL (no module).

- [ ] **Step 3: Implement.** Reuse `scan._git`; extend it with an `env` that adds `GIT_OPTIONAL_LOCKS=0`, and a bytes variant for `git show`, rather than adding a second runner.

- [ ] **Step 4: Run** `tests/unit/test_changes_*.py tests/unit/test_cli_changes.py`. Expect PASS.

- [ ] **Step 5: Commit** — `feat(changes): due list, owner-requested rollback and governance apply for runs`

---

### Task 7: The materializer records 🔴 items left untouched for 3 days

**Files:**
- Create: `plugin/engine/scout/changes/carried.py`
- Modify: `plugin/engine/scout/action_items/materialize.py` (one call after the day's file is written)
- Test: `plugin/engine/tests/unit/test_changes_carried.py`, `plugin/engine/tests/unit/test_action_items_materialize.py`

**Interfaces:**
- Consumes: `scout.action_items.parser.parse_lines` (`ActionItem.priority`, `.status`, `.short_prefix`); `scout.action_items.stale.last_changed_by_tag(lines, *, data_dir, today)`; `append_signal_once` (Task 1).
- Produces: `RED_AFTER_DAYS = 3`; `record_red_carried(vault: Path, lines: list[str], *, today: date, now: datetime, env: Mapping[str, str]) -> int`.
  - **Which items:** each open or in-progress `🔴` item with a `short_prefix` whose `today - last_changed >= 3 days` gets one row.
  - **The row:** `red-carried`, surface `action-items`, `ts=now`.
  - **The ref:** `sha256(short_prefix)[:16] + "@" + last_changed.isoformat()`, so a touched-then-stale-again item counts again.
  - **Result:** it returns the rows written.
  - `materialize()` calls it inside `try/except Exception: pass` after writing the file. A failure never changes materialize's return or raises.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_red_item_untouched_3_days_is_recorded_once(tmp_path): ...   # run twice -> 1 row
def test_a_red_item_untouched_2_days_is_not(tmp_path): ...
def test_yellow_done_and_untagged_items_are_skipped(tmp_path): ...
def test_touched_then_stale_again_counts_again(tmp_path): ...
def test_ref_holds_no_title_text(tmp_path): ...                         # the item's title words are not in the row
def test_materialize_survives_a_recorder_error(tmp_path, monkeypatch): ...  # record_red_carried raises -> file still created
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_changes_carried.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement.** Build the daily files in the tests the way `test_action_items_materialize.py` already does.

- [ ] **Step 4: Run** `tests/unit/test_changes_carried.py tests/unit/test_action_items_materialize.py tests/unit/test_materialize_carried_day_headings.py`. Expect PASS.

- [ ] **Step 5: Commit** — `feat(changes): a 🔴 left untouched for 3 days is recorded as noise, once`

---

### Task 8: Phases — runs record the signals and act on the owner's decisions

**Files:**
- Modify: `plugin/phases/modes/feedback-processing.md`, `plugin/phases/core/privacy.md`, `plugin/phases/connectors/calendar.md` (inbound scan section), `plugin/phases/core/git-setup.md` ("Changing the vault's git"), `plugin/phases/modes/wishlist.md` (the SKILL.md routing bullet), `plugin/phases/core/action-items.md` (the "approved-but-unapplied proposal" blocker), `plugin/phases/modes/kb-deep-work.md` (the route list)
- Test: `plugin/engine/tests/unit/test_phase_changes_map.py`

**Interfaces:**
- Consumes: every CLI command from Tasks 1, 5 and 6, and the ref formats in "Decisions".
- Produces: phase text only. The required content, which the tests pin:
  - **`feedback-processing.md`**
    - **Step 1a.**
      - Always re-read the bot's DMs from the trailing 7 days, including top-level owner messages; a top-level message counts as a reply to the newest bot message in the 48 h before it.
      - Record each of the owner's replies and reactions with `scripts/scoutctl engagement record dm-reply|dm-reaction --surface slack-dm --ref <bot ts>/<reply ts>` (reactions `<bot ts>/r:<emoji>`), with `--ts <its Slack ts>` and `--sentiment`. Add `--change <chg-id>` when a negative reply names a change on the map.
      - `already recorded` means an earlier run handled it, so skip it in Steps 1b–1d.
    - **Step 1c.** Each recurrence records `scoutctl engagement record recurrence --surface mistake-audit --ref pattern-<N>@<evidence Slack ts or date>`.
    - **Step 1d.**
      - The `SKILL.md` row says direct edit for everything except governance or safety gating.
      - That one goes to a `governance-pending/<slug>.md` file (format: frontmatter `summary`, `why`, `targets`, then one fenced `diff` block) and is never applied by the run that wrote it.
      - The harness fallback writes the same file.
      - No mention of `dreaming-proposals.md`.
    - **Step 1e** is renamed "Test changes and act on your decisions":
      - run `scripts/scoutctl changes due --json`;
      - for each rollback, run `scoutctl changes rollback <id>`, then commit the printed paths with trailers `Scout-Change-Reverts: <id>`, `Scout-Change-Why: owner-ask:undo-<id>` (or `verdict:<id>` for auto) and `Scout-Change-Summary:`;
      - `apply` / `discard` work the same way, `apply` with `Scout-Change-Governance: yes`;
      - list each `blocked` item in the wrap with its reason in plain words;
      - each `suspect_unannounced` gets a plain-words wrap line ending "Undo in the Changes tab, or run `scoutctl changes undo <id>`", then `scoutctl changes announced <ids>`;
      - `engagement_tests_running` ≥ 1 means no new engagement-targeted change this run;
      - `calibration_report_due` means write `docs/changes-calibration-report.md` (verdict counts, how many the owner marked wrong, `can't tell` count, proposed thresholds) and file any threshold change as a governance-pending item;
      - the back-port reminder now fires on applied governance patches instead of applied proposals.
    - **Step 1f** lists the trailers.
  - **`privacy.md`**: after a Slack send to the owner's DM returns a ts, and only then, run `scripts/scoutctl engagement record dm-sent --surface slack-dm --ref <ts>`. Never for channels, threads or other people.
  - **`calendar.md`**: in the inbound scan, when today has an all-day out-of-office, travel or leave event of the owner's, run `scripts/scoutctl engagement record owner-away --surface calendar --ref <YYYY-MM-DD>`.
  - **`git-setup.md`**: a run's commit that touches `SKILL.md`, `DREAMING.md`, `RESEARCH.md` or `scripts/` carries `Scout-Change-Why:` (`pattern-<N>`, `owner-ask:<Slack ts>`, `wishlist:<slug>`, `research:<slug>`) and `Scout-Change-Summary:` (one plain sentence, ≤ 120 characters). `Scout-Change-Targets:` and `Scout-Change-Governance: yes` are optional. Show one example built with `$'…\n\n…'`.
  - **`wishlist.md`, `action-items.md` and `kb-deep-work.md`**: governance routes to `governance-pending/`, never to `dreaming-proposals*`.

- [ ] **Step 1: Write the failing tests** (pattern: `tests/unit/test_phase_engine_edit_rule.py`, using `parse_phase_file`)

```python
def test_feedback_processing_rereads_seven_days_and_records_signals(): ...
    # body contains "trailing 7 days", "engagement record dm-reply", "--ref <bot ts>/", "already recorded",
    # "engagement record recurrence"
def test_step_1e_acts_on_due(): ...
    # contains "scoutctl changes due --json", "Scout-Change-Reverts:", "scoutctl changes announced", "governance-pending/"
def test_no_phase_routes_to_dreaming_proposals(): ...   # no file under phases/ contains "dreaming-proposals"
def test_dm_sent_recorded_only_after_an_owner_dm_send(): ...   # privacy.md contains "engagement record dm-sent"
def test_owner_away_is_recorded_by_the_inbound_calendar_scan(): ...
def test_git_setup_names_the_trailers(): ...            # "Scout-Change-Why:" and "Scout-Change-Summary:" in all four modes' section
def test_dreaming_brain_carries_the_test_loop(): ...
    # bootstrap._assemble(BootstrapConfig(plugin_root=<repo plugin dir>, ...), "DREAMING") contains "scoutctl changes due --json";
    # the "SKILL" assembly with calendar+slack enabled contains "owner-away" and "dm-sent"
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_phase_changes_map.py -v`. Expected: FAIL.

- [ ] **Step 3: Edit the phase files.** Keep each edit inside its named section, and keep the cold-reader style: gloss `chg-…` ids as "the change's id on the map".

- [ ] **Step 4: Run** `tests/unit/test_phase_*.py tests/unit/test_kb_write_protocol_assembly.py tests/unit/test_custom_assembly.py`, plus `tests/integration/test_git_safe_commit.py`. Expect PASS. If an existing test pins removed proposal text, update its assertion to the governance-pending wording and say so in the report.

- [ ] **Step 5: Commit** — `feat(phases): runs record DM, recurrence and away signals and act on change decisions`

---

### Task 9: Changelog fragment and full verification

**Files:**
- Create: `plugin/changelog.d/changes-map-plan-2.added.md`

- [ ] **Step 1: Write the fragment.** Three to five bullets in plain words:
  - verdicts per change;
  - DMs left unanswered for 48 h and 🔴s untouched for 3 days count as noise;
  - owner commands (`approve`, `reject`, `undo`, `wrong`, `auto-rollback`, `calibrated`);
  - runs act on `changes due`;
  - governance waits in `governance-pending/`.
  Never edit `plugin/CHANGELOG.md`.
- [ ] **Step 2: Run** `.venv/bin/python -m scout.scripts.changelog check --base origin/main --target-branch main --event pull_request` from `plugin/engine`. Expected: exit 0.
- [ ] **Step 3: Run lint and types:** `.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout`. Expected: clean.
- [ ] **Step 4: Run the full suite under the shared build lock:**

```bash
until mkdir ~/.scout-worktrees/.build-lock 2>/dev/null; do sleep 30; done; trap 'rmdir ~/.scout-worktrees/.build-lock' EXIT
nice -n 10 .venv/bin/python -m pytest tests -n 8 --cov=scout --cov-fail-under=98 -q
```

Expected: all pass, coverage ≥ 98 %, and the same skip and warning counts as `main`.

- [ ] **Step 5: Commit** — `docs(changelog): changes map part 2`
