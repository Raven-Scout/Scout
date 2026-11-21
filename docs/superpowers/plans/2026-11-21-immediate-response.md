# Immediate Response (Telegram) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Within about 5 minutes of the owner messaging the Scout Telegram bot, acknowledge
the message with 👀. Then start a narrow background "respond" run that handles every
pending message and sends one combined reply in the chat.

**Architecture:** This adds a `telegram` source to the existing trigger engine, which
already runs on the 5-minute `schedule tick`. Only trusted, unsandboxed code writes the
message ledger in `.scout-state/telegram-inbound/`: the tick, and `cli_send`, which runs
directly or inside the broker. That folder is protected from runs. Runs read a snapshot of
the messages, and prove they answered by replying with `--reply-to` / `--covers`. The
respond run is a new brain kind (`RESPOND.md`) with its own runner (`run-respond.sh`).
Scheduled runs answer any leftover messages through session-context.

**Tech Stack:** Python 3 engine (`plugin/engine/scout`), Typer CLI, `requests`, pytest,
bash runner templates, and markdown phase files.

**Spec:** `docs/superpowers/specs/2026-11-21-immediate-response-design.md` (approved
2026-11-21). Read it before any task.

## Global Constraints

- **Paths.** All paths are relative to the worktree root (the checkout of this branch). The
  engine lives at `plugin/engine/`.
- **Tests.** Run them from `plugin/engine` with `.venv/bin/python -m pytest …`. Create the
  venv once with `uv venv && uv pip install -e ".[dev,full]"`.
- **Snapshots.** Regenerate them with their scripts, never by hand. For the schedule
  snapshot: `.venv/bin/python -m scout.scripts.schedule_snapshot --no-also-write-app-fixture`.
- **Never run** `release-app.sh`, `release-plugin.sh` or any release script. Never pass
  `--no-verify`.
- **Public repo.** `Raven-Scout/Scout` is public. No client names, people, or vault
  content go in code, tests, commit messages or the PR. Use generic examples.
- **Values fixed by the spec:**
  - `daily_fire_cap: 20`, `cooldown_seconds: 600`
  - dispatch timeout **60 min**, **3** attempts before `failed`
  - cheap ack: **≤15 chars**
  - ack emoji **👀**, with "On it" as the fallback
  - text and captions only
  - private chat only, where `chat.id == from.id == telegram-chat-id`
- **Commits** go on branch `design/immediate-response`, in conventional form
  (`feat(triggers): …`, `test: …`), and end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Deliberate trims from the spec**, which the owner was told about:
  - no `telegram_inbound.*` event-stream events
  - no `connector-health` row

  `scoutctl telegram inbound-status` (Task 9) covers observability instead. Every other
  spec requirement maps to a task.

## Review Focus

1. **The Mac was asleep for hours, and 30+ messages are pending.** Expect one event, one
   run, and one reply under the broker's 8000-char limit. Owner: Task 4 adds
   `test_backlog_of_many_messages_yields_one_event`. Task 6's phase text tells the run to
   keep the reply to one line per message and summarize past 20.
2. **The owner sends more messages while a respond run is in flight.** No second fire
   happens until the run's reply lands or times out; after that the new message fires on
   its own. Owner: Task 4 adds `test_new_message_during_inflight_run_waits_then_fires`.
3. **The reply was queued by the broker and lands after the message timed out back to
   pending.** It must still mark the message answered, with no duplicate run. Owner: Task 1
   adds `test_record_reply_answers_a_message_that_was_reverted_to_pending`.
4. **The Telegram API is down, returns 409, or the token was revoked.** The ledger and
   offset don't change, the error is recorded for `inbound-status`, and the tick doesn't
   crash. Owner: Task 4 adds `test_api_error_leaves_ledger_and_offset_untouched`.
5. **The owner sends a photo, sticker or voice note.** The tick replies "not supported
   yet" once, with no run. Owner: Task 4 adds
   `test_unsupported_message_gets_one_plain_reply_and_no_run`.

---

### Task 1: Message ledger and cheap-message classifier

**Files:**
- Create: `plugin/engine/scout/telegram_inbound/__init__.py`, `ledger.py`, `classify.py`
- Test: `plugin/engine/tests/unit/test_telegram_inbound_ledger.py`, `test_telegram_inbound_classify.py`

**Interfaces:**
- Produces, in `ledger.py`:
  - **Constants:**
    - `LEDGER_DIRNAME = "telegram-inbound"`
    - `DISPATCH_TIMEOUT = dt.timedelta(minutes=60)`, `MAX_ATTEMPTS = 3`
    - states `PENDING="pending"`, `DISPATCHED="dispatched"`, `ANSWERED="answered"`,
      `SKIPPED_CHEAP="skipped_cheap"`, `SKIPPED_UNSUPPORTED="skipped_unsupported"`,
      `FAILED="failed"`
  - `def ledger_dir(vault: Path) -> Path` returns `paths.state_dir(vault) / LEDGER_DIRNAME`.
  - `@dataclass class InboundMessage`:
    - `message_id: int`, `date: int`, `text: str`, `reply_to_text: str | None`,
      `forwarded: bool`
    - `kind: str` (`"text"` or `"unsupported"`)
    - `state: str = PENDING`, `attempt: int = 0`, `dispatched_at: str | None = None`,
      `acked: bool = False`, `answered_at: str | None = None`
  - `class Ledger(vault: Path)`, with these methods:
    - `locked()`: a context manager that takes an exclusive `flock` on
      `ledger_dir/.lock`. Every mutating method requires it.
    - `add(msg) -> bool`: False if the id already exists.
    - `get(message_id) -> InboundMessage | None`
    - `pending() -> list[InboundMessage]`
    - `in_flight(now) -> list[InboundMessage]`
    - `mark_acked(ids: list[int])`
    - `mark_skipped(ids, state)`
    - `mark_dispatched(ids: list[int], *, now)`: sets `attempt += 1` and `dispatched_at`.
    - `unanswered_ids() -> set[int]`: messages in `pending` or `dispatched`.
    - `record_reply(*, reply_to: int, covers: list[int], at: dt.datetime) -> None`: appends
      `{reply_to, covers, delivered_at}` to `replies.jsonl`, and marks every id in
      `covers ∪ {reply_to}` answered, whatever its current state.
    - `reconcile(*, now, run_alive: bool) -> list[InboundMessage]`: reverts timed-out
      dispatched messages to pending when `not run_alive`, and moves a message to failed
      once its attempt count reaches `MAX_ATTEMPTS`. Returns the messages that just failed.
    - `counts() -> dict[str, int]`
    - `get_poll_state() -> dict` and `set_poll_state(*, offset: int, last_poll_at: str,
      last_error: str | None)`, stored in `offset.json`.
  - Storage: `messages.json`, keyed by message_id, written atomically through
    `scout.safe_write`.
- Produces, in `classify.py`:
  - `CHEAP_ACKS = frozenset({"thanks", "thank you", "thx", "ty", "ok", "okay", "got it", "cool", "nice"})`
  - `def is_cheap(text: str) -> bool`: True when the stripped, case-folded text is at most
    15 chars **and** is either in `CHEAP_ACKS` or contains no letters or digits
    (emoji / punctuation only).

- [ ] **Step 0:** Create the venv: `cd plugin/engine && uv venv && uv pip install -e ".[dev,full]"`. Confirm it with `.venv/bin/python -m pytest tests/unit/test_triggers_evaluate.py -q`, which should pass.
- [ ] **Step 1: Write the failing tests:**
  - `test_add_is_idempotent_by_message_id`
  - `test_mark_dispatched_increments_attempt_and_sets_time`
  - `test_in_flight_excludes_dispatches_older_than_60_minutes`
  - `test_reconcile_reverts_timed_out_dispatch_when_no_run_alive`
  - `test_reconcile_keeps_dispatch_while_run_alive`
  - `test_third_timeout_moves_message_to_failed_and_returns_it`
  - `test_record_reply_marks_reply_to_and_covers_answered`
  - `test_record_reply_answers_a_message_that_was_reverted_to_pending` (Review Focus 3)
  - `test_unanswered_ids_is_pending_plus_dispatched`
  - `test_poll_state_round_trips`
  - `test_ledger_survives_corrupt_messages_json`: a corrupt file is read as empty and
    renamed `.corrupt-<ts>`.

  In the classifier tests: `is_cheap("Thanks!")`, `is_cheap("👍")`, `is_cheap("ok")` are
  True. `is_cheap("ok drop the invoice item")` and `is_cheap("why?")` are False.
  `is_cheap("")` is True.
- [ ] **Step 2:** Run `.venv/bin/python -m pytest tests/unit/test_telegram_inbound_ledger.py tests/unit/test_telegram_inbound_classify.py -q`. Expected: FAIL (module not found).
- [ ] **Step 3:** Implement `ledger.py` and `classify.py` to the interfaces above. Use `fcntl.flock`, following `scout/connector_calls.py:64`.
- [ ] **Step 4:** Re-run the tests. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(telegram-inbound): message ledger and cheap-ack classifier`.

### Task 2: Protect the ledger folder from runs

**Files:**
- Modify: `plugin/engine/scout/kb/hook_paths.py` (category constants near :68-111, and `protected_specs` near :376)
- Test: `plugin/engine/tests/unit/test_hooks_write_gate.py`

**Interfaces:**
- Consumes `LEDGER_DIRNAME` from Task 1.
- Produces `TELEGRAM_INBOUND = "Scout's Telegram inbound ledger"`, and
  `add(vault / ".scout-state" / LEDGER_DIRNAME, TELEGRAM_INBOUND, tree=True)`.

- [ ] **Step 1:** Add `test_a_run_cant_write_the_telegram_inbound_ledger`, modeled on `test_a_run_cant_write_the_delegate_state` (~:913). It should cover both a file inside the folder and the folder itself.
- [ ] **Step 2:** Run that test. Expected: FAIL.
- [ ] **Step 3:** Add the constant and the `add(...)` line.
- [ ] **Step 4:** Run `test_hooks_write_gate.py` and `test_run_sandbox_profile.py`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(sandbox): protect the Telegram inbound ledger from runs`.

### Task 3: Bot API helpers

**Files:**
- Create: `plugin/engine/scout/telegram_inbound/bot.py`
- Test: `plugin/engine/tests/unit/test_telegram_inbound_bot.py`

**Interfaces:**
- Consumes `InboundMessage` (Task 1), plus `_read_secret` and `TELEGRAM_API` from
  `scout/scripts/notify_telegram.py`.
- Produces:
  - `class TelegramApiError(Exception)`. Its message never contains the token.
  - `HttpPost = Callable[..., dict]`: `(url, *, json, timeout) -> dict`.
  - `def read_credentials() -> tuple[str, int]`: returns `(token, chat_id)`. Raises
    `ConfigError` when either is missing.
  - `def get_updates(*, token: str, offset: int, http_post: HttpPost, timeout: float = 10.0) -> list[dict]`:
    sends `{"offset": offset, "timeout": 0, "allowed_updates": ["message"]}`.
  - `def set_reaction(*, token, chat_id: int, message_id: int, emoji: str = "👀", http_post) -> bool`:
    calls `setMessageReaction` and returns False on any error.
  - `def send_plain_reply(*, token, chat_id: int, reply_to: int, text: str, http_post) -> bool`:
    uses `sendMessage`, `disable_notification: True`, and
    `reply_parameters: {message_id, allow_sending_without_reply: True}`.
  - `def parse_message(update: dict, *, owner_chat_id: int) -> InboundMessage | None`:
    - returns None unless the update has `message`, the chat type is `"private"`, and
      `chat.id == from.id == owner_chat_id`
    - `text` comes from `text`, else `caption`, else `""`
    - `kind` is `"unsupported"` when neither exists
    - `reply_to_text` comes from `reply_to_message.text`
    - `forwarded` is set when `forward_origin` is present

- [ ] **Step 1: Write the failing tests:**
  - `test_parse_accepts_owner_private_text`
  - `test_parse_rejects_other_chat_and_other_sender`
  - `test_parse_rejects_group_chat`
  - `test_parse_uses_caption_when_no_text`
  - `test_parse_marks_sticker_unsupported`
  - `test_parse_keeps_reply_to_text_and_forwarded_flag`
  - `test_get_updates_raises_scrubbed_error_on_not_ok`: the token string is absent from
    `str(exc)`.
  - `test_set_reaction_returns_false_on_http_error`
  - `test_send_plain_reply_sets_reply_parameters_and_silent`

  All of them use a fake `http_post` that records calls.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement `bot.py`.
- [ ] **Step 4:** Run them. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(telegram-inbound): Bot API helpers`.

### Task 4: Telegram trigger source, registry, and the engine's on-fired hook

**Files:**
- Create: `plugin/engine/scout/triggers/sources/telegram.py`
- Modify:
  - `plugin/engine/scout/triggers/sources/__init__.py` (`SOURCE_NAMES` :18, `get_source` :21)
  - `plugin/engine/scout/triggers/engine.py` (after `dispatch` at :134)
- Test:
  - `plugin/engine/tests/unit/test_triggers_source_telegram.py`
  - `plugin/engine/tests/unit/test_triggers_evaluate.py`

**Interfaces:**
- Consumes Tasks 1 and 3, plus
  `scout.scripts.bootstrap_lock.is_lock_held_by_live_pid` and `kb.live_run.LOCK_NAME`.
- Produces:
  - `SUPPORTED_MATCH_TYPES = ["message"]`
  - `class TelegramSource`, with `name = "telegram"`:
    - `__init__(self, *, vault: Path, credentials: Callable[[], tuple[str, int]] | None = None, http_post: HttpPost | None = None, lock_held: Callable[[], bool] | None = None, clock: Callable[[], dt.datetime] | None = None)`
    - `@classmethod for_vault(cls, vault)`
  - `TelegramSource.scan_since(ts) -> list[ConnectorEvent]`. It ignores `ts`. Everything
    runs under `Ledger.locked()`, in this order:
    1. Call `get_updates` from the stored offset.
    2. Pass each update through `parse_message`, then `ledger.add`.
    3. Acknowledge each new message:
       - **cheap:** mark `skipped_cheap`, with no ack
       - **unsupported:** send one `send_plain_reply` with
         `"I can only read text messages for now."`, then mark `skipped_unsupported`
       - **otherwise:** `set_reaction`, falling back to
         `send_plain_reply(text="On it")`, then `mark_acked`
    4. `set_poll_state(offset=max(update_id)+1, …)`. Only after the ledger writes.
    5. `reconcile(now, run_alive=lock_held())`. For each message that just failed, send
       `send_plain_reply(text="I couldn't handle this message after 3 tries. It's logged in Scout.")`.
    6. If `pending()` is non-empty **and** `in_flight(now)` is empty **and** `not lock_held()`,
       return exactly one `ConnectorEvent`:
       - `source="telegram"`, `source_event_id=f"{max_pending_id}:{max_attempt+1}"`
       - `normalized_match_fields={"type": "message"}`
       - `raw_payload={"messages": [asdict(m) for m in pending]}`

       Otherwise return `[]`.
    7. On an API error: record `last_error`, leave the offset unchanged, and return `[]`.
  - `TelegramSource.on_fired(event: ConnectorEvent, outcome: FireOutcome) -> None`. Import
    `FireOutcome` under `TYPE_CHECKING` only, because `dispatcher` → `actions` →
    `config` → `sources` would otherwise be an import cycle. When
    `outcome.status == "ok"`, call `mark_dispatched` on the ids in `raw_payload["messages"]`.
  - `health_check()`: returns False with a reason when the credentials are missing, the
    chat id is negative (`"group chats unsupported"`), or `last_error` is set from the last
    poll. Otherwise `(True, "ok")`.
  - `supports_webhook()`: returns False.
  - Registry: `SOURCE_NAMES` gains `"telegram"`, and `get_source` gains a
    `"telegram" → TelegramSource.for_vault(vault)` branch.
  - Engine: after `dispatch(...)` succeeds and is recorded, call
    `getattr(source, "on_fired", None)`. If it is callable, call it as
    `hook(event, outcome)` inside a try that appends to `result.errors` on an exception.

- [ ] **Step 1: Write the failing source tests**, using a fake `http_post` and `lock_held`, and a `fake_data_dir` vault:
  - `test_new_message_is_acked_with_reaction_and_returns_one_event`
  - `test_reaction_failure_falls_back_to_on_it_reply`
  - `test_cheap_message_is_skipped_without_ack_or_event`
  - `test_unsupported_message_gets_one_plain_reply_and_no_run` (Review Focus 5)
  - `test_offset_advances_only_after_ledger_write`
  - `test_api_error_leaves_ledger_and_offset_untouched` (Review Focus 4)
  - `test_no_event_while_session_lock_held`
  - `test_backlog_of_many_messages_yields_one_event`: 30 messages give one event with 30
    payload entries (Review Focus 1).
  - `test_new_message_during_inflight_run_waits_then_fires` (Review Focus 2)
  - `test_event_id_changes_on_retry_after_timeout`
  - `test_failed_message_sends_one_notice`
  - `test_health_check_rejects_group_chat_id`
- [ ] **Step 2: Write the failing engine tests** in `test_triggers_evaluate.py`:
  - `test_on_fired_hook_called_after_successful_dispatch`
  - `test_on_fired_exception_is_recorded_not_raised`

  Also extend `FakeSource` with an optional `on_fired` recorder.
- [ ] **Step 3:** Run both files. Expected: FAIL.
- [ ] **Step 4:** Implement the source, the registry entries, and the engine hook.
- [ ] **Step 5:** Run `tests/unit/test_triggers_*.py` and `test_trigger_pipeline_edges.py`. Expected: PASS. Also add an end-to-end test, `test_telegram_trigger_spawns_run_skill_and_marks_dispatched`, in `test_trigger_pipeline_edges.py`. It needs a `triggers.yaml` with `runner: run-respond.sh`, so mark it `xfail` until Task 7 adds that runner to `RUNNERS`, and remove the mark in Task 7.
- [ ] **Step 6:** Commit: `feat(triggers): Telegram inbound source`.

### Task 5: `notify telegram --reply-to / --covers`, recorded by trusted code

**Files:**
- Modify:
  - `plugin/engine/scout/scripts/notify_telegram.py`: `_build_payload` :567, `send` :650, `cli_send` :790
  - `plugin/engine/scout/cli.py`: `cli_notify_telegram` :3735, and the broker fields at :3790
  - `plugin/engine/scout/sandbox/broker.py`: `validate_telegram` :254, `send_telegram` :337
- Test:
  - `plugin/engine/tests/unit/test_scripts_notify_telegram.py`
  - `plugin/engine/tests/unit/test_run_broker.py`

**Interfaces:**
- Consumes `Ledger.unanswered_ids` and `Ledger.record_reply` (Task 1).
- Produces:
  - `_build_payload(..., reply_to: int | None = None)` adds
    `"reply_parameters": {"message_id": reply_to, "allow_sending_without_reply": True}`.
  - `send(..., reply_to=None)`
  - `cli_send(..., reply_to: int | None = None, covers: list[int] | None = None, vault: Path | None = None)`:
    - the full set is `covers ∪ {reply_to}`
    - if any id in it is not in `Ledger(vault).unanswered_ids()`, return exit 1 with
      `"unknown or already answered message ids"`, and don't send
    - after a successful (exit 0) send, call `record_reply`
    - `vault` defaults to `paths.data_dir()`
  - Typer gains `--reply-to INT` and `--covers TEXT`, a comma-separated list of ints.
  - The broker's `validate_telegram` accepts `reply_to` (an int > 0, or absent) and
    `covers` (a list of at most 100 positive ints), and refuses anything else with
    `EXIT_REFUSED`. It returns both in `fields`. `send_telegram` passes them to `cli_send`.
    Queue replays go through the same validation.

- [ ] **Step 1: Write the failing notify tests:**
  - `test_reply_to_sets_reply_parameters`
  - `test_covers_unknown_id_refuses_without_sending`
  - `test_successful_reply_records_answered_in_ledger`
  - `test_failed_send_does_not_record_reply`
- [ ] **Step 2: Write the failing broker tests:**
  - `test_broker_passes_reply_to_and_covers_through`
  - `test_broker_refuses_non_int_covers`
  - `test_queued_reply_with_covers_is_validated_on_drain`
- [ ] **Step 3:** Run both files. Expected: FAIL.
- [ ] **Step 4:** Implement the changes, including the CLI options and the client `fields` dict.
- [ ] **Step 5:** Run `test_scripts_notify_telegram*.py`, `test_notify_telegram_*.py`, `test_run_broker*.py` and `test_cli_surface.py`. Expected: PASS.
- [ ] **Step 6:** Commit: `feat(notify): Telegram replies that answer inbound messages`.

### Task 6: The RESPOND brain and its phase

**Files:**
- Create: `plugin/phases/respond/respond.md`
- Modify:
  - `plugin/engine/scout/scripts/brain_merge.py:36` (`BRAIN_KINDS`)
  - `plugin/engine/scout/scripts/bootstrap.py:531-539` (`_assemble`)
  - `plugin/engine/scout/scripts/custom_assembly.py:25` (`TARGET_MODES`)
  - `plugin/engine/scout/scripts/phase_backport.py:33` (`_ASSEMBLY_MAP`)
  - `plugin/engine/scout/changes/scan.py:22` (`BRAIN_FILES`)
  - `plugin/engine/scout/diagnostics.py:62,83`
  - `plugin/engine/scout/cli.py:4260-4276`
  - the `mode:` frontmatter in `plugin/phases/core/privacy.md` and
    `plugin/phases/core/git-setup.md`
- Test:
  - `plugin/engine/tests/unit/test_phase_assembly.py`
  - `plugin/engine/tests/unit/test_phase_engine_edit_rule.py:34`

**Interfaces:**
- Produces:
  - Brain kind `"RESPOND"`, with `_assemble(cfg, "RESPOND")` built from `[core, respond]`
    phases filtered to modes `{"respond"}`. Installed to `vault/RESPOND.md` by the existing
    `BRAIN_KINDS` loops.
  - `respond.md` frontmatter: `phase: respond`, `name: respond`, `slot: respond`,
    `mode: [respond]`, `requires: telegram`.
  - The body says, in order:
    1. Read the messages from `$SCOUT_TRIGGER_EVENT_PATH` → `event.raw_payload.messages`.
       If that's unset, read session-context `telegram_pending`.
    2. Read today's action items, and only the KB files the messages touch. Don't run a
       full sweep.
    3. Duplicate guard: `git log --grep "tg:<id>"`, and skip work already done.
    4. Handle each message by kind, as in spec §4.
    5. Commit `respond [HH:MM]: …`, with a `tg:<id>` line per message in the body.
    6. Send one reply:
       `scoutctl notify telegram --tier info --parse-mode html --reply-to <newest id> --covers <all ids> --body-file -`.
       One line per message; past 20 messages, summarize the rest.
    7. Spell out the guardrails list from spec §4 verbatim.
- `respond` is added to the `mode:` lists of `core/privacy.md` and `core/git-setup.md`.

- [ ] **Step 1: Write the failing tests:**
  - `test_respond_brain_includes_respond_privacy_and_git_setup`
  - `test_respond_brain_excludes_slack_and_telegram_wrap_sections`
  - `test_brain_kinds_include_respond`

  Update the expected mode set in `test_phase_engine_edit_rule.py:34` to include `respond`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Write `respond.md`, add the `RESPOND` branch and its map entries, and edit the two core phases' `mode:` lists.
- [ ] **Step 4:** Run `tests/unit/test_phase_*.py`, `test_bootstrap*.py`, `test_custom_assembly*.py`, `test_changes*.py` and `test_diagnostics*.py`. Expected: PASS. A test that asserts exactly three brains should be updated to four.
- [ ] **Step 5:** Commit: `feat(brains): RESPOND brain for immediate replies`.

### Task 7: `run-respond.sh` runner and the `scout-respond` skill

**Files:**
- Create:
  - `plugin/templates/run-respond.sh.tmpl`, copied from `run-dreaming.sh.tmpl`
  - `plugin/skills/scout-respond/SKILL.md`, modeled on `plugin/skills/scout-dream/SKILL.md`
- Modify:
  - `plugin/engine/scout/scripts/bootstrap.py:237` (`_CAT1B_RUNNERS`)
  - `plugin/engine/scout/schedule.py:36` (`RUNNERS`)
  - `plugin/engine/scout/kb/hook_paths.py:121` (`RUNNER_FILES`)
  - `plugin/engine/scout/health.py:345` (`_RUNNERS`)
  - `plugin/engine/scout/hooks/run_context.py:34`, where the regex gains `RESPOND`
  - `plugin/engine/scout/schedule.snapshot.json`, regenerated
- Test: update the runner lists in `test_runner_exports_scout_mode.py:28`,
  `test_runner_log_name_upgrade.py:28`, `test_agent_runtime_claude_code.py:17`,
  `test_delegate_sync_schedule.py:266`, `test_gate_probe.py:388` and
  `test_template_manifest.py:35`. Also add `test_run_respond_template_pins_mode_and_budget`.

**Interfaces:**
- Produces:
  - **The template.** Compared with `run-dreaming.sh.tmpl` it differs only in these:
    - `MODE=respond` is fixed, because `run_skill` sets `SCOUT_FORCE_MODE=scout-respond`
      and the template ignores it.
    - `RUNNER_KIND=respond`, with the log at `respond-${STAMP}.log`.
    - The prompt is "running in RESPOND mode … Read RESPOND.md in full and follow it",
      followed by the `$SCOUT_TRIGGER_EVENT_PATH` hint.
    - Pre-session runs with session type `respond`.
    - It calls `model resolve --slot respond` and `budget record-run "respond"`.
  - **The skill.** Its frontmatter has `name: scout-respond` and the description
    "Answer pending Telegram messages from the owner now (normally started automatically
    by the telegram-respond trigger)". Its body launches `./run-respond.sh` in the
    background, the way scout-dream does.

- [ ] **Step 1:** Write `test_run_respond_template_pins_mode_and_budget`, which renders the template and asserts `MODE=respond`, `record-run "respond"` and `RESPOND.md`. Add `run-respond.sh` to the parametrized runner lists in the tests above.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Create the template and the skill, and update the five constant lists.
- [ ] **Step 4:** Regenerate the schedule snapshot, remove the Task 4 `xfail`, and run `tests/unit -q -x`. Expected: PASS. If `gather("respond")` in pre-session fails on an unknown session type, make it treat `respond` like a minimal session, and add `test_gather_accepts_respond_session_type`.
- [ ] **Step 5:** Commit: `feat(runner): run-respond.sh and scout-respond skill`.

### Task 8: Scheduled runs drain leftover messages

**Files:**
- Modify:
  - `plugin/engine/scout/scripts/pre_session_data.py`: the `SessionContext` dataclass at
    :77, `gather()` at :447, and `__all__`
  - `plugin/phases/connectors/telegram.md`
- Test: `plugin/engine/tests/unit/test_pre_session_data*.py`

**Interfaces:**
- Consumes `Ledger.unanswered_ids` and `Ledger.get` (Task 1).
- Produces:
  - `SessionContext.telegram_pending: list[dict]`, the unanswered messages with `state`
    and `attempt`.
  - `def get_telegram_pending(scout_dir: Path) -> list[dict]`: never raises, and returns
    `[]` on any error. It is filled only when the reader is full, using the
    `... if full else []` pattern.
  - In `connectors/telegram.md`, a new `### Messages waiting from {{USER_NAME}}` section
    before "How to send". It says: if session-context `telegram_pending` is non-empty,
    handle them under the RESPOND rules (§4), then send one reply with
    `--reply-to <newest> --covers <all>` in addition to the wrap.

- [ ] **Step 1: Write the failing tests:**
  - `test_gather_includes_telegram_pending_for_full_reader`
  - `test_gather_omits_telegram_pending_for_restricted_reader`
  - `test_get_telegram_pending_returns_empty_on_corrupt_ledger`
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement the field and the helper, and add the phase section.
- [ ] **Step 4:** Run `test_pre_session_data*.py`, `test_session_context*.py` and `test_phase_*.py`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(pre-session): surface pending Telegram messages to scheduled runs`.

### Task 9: `scoutctl telegram` (find-chat-id, inbound-status) and docs

**Files:**
- Modify:
  - `plugin/engine/scout/cli.py`: a new `_register_telegram()`, next to `_register_notify()` at :3723
  - `plugin/engine/scout/docs/connectors/telegram-setup.md:82-97`
  - `plugin/commands/scout-status.md`
  - `plugin/CHANGELOG.md`, under Unreleased
- Test: `plugin/engine/tests/unit/test_cli_telegram.py`

**Interfaces:**
- Consumes `get_updates` (Task 3) and the `Ledger` (Task 1).
- Produces:
  - `scoutctl telegram find-chat-id`: calls `get_updates` with `offset=0`, so it **never
    confirms**, and prints each distinct private `chat.id` with the sender's first name.
    If nothing is found it prints a hint to send the bot a message, and says the inbound
    trigger may already have consumed the updates.
  - `scoutctl telegram inbound-status [--json]`: prints `counts()`, `last_poll_at` and
    `last_error`, and exits 0.
  - The setup doc replaces the raw `curl …/getUpdates` with `scoutctl telegram find-chat-id`.
  - The status command gets a line that runs `scoutctl telegram inbound-status`.
  - The CHANGELOG entry also explains how to turn the feature on: create
    `<vault>/.scout-state/triggers.yaml` with the spec's single trigger, including
    `runner: run-respond.sh`.

- [ ] **Step 1: Write the failing tests:**
  - `test_find_chat_id_never_passes_a_confirming_offset`
  - `test_find_chat_id_prints_private_chat_ids`
  - `test_inbound_status_json_shape`
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement the commands, and edit the docs and the CHANGELOG.
- [ ] **Step 4:** Run `test_cli_telegram.py`, `test_manifest.py` and `test_cli_surface.py`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(cli): scoutctl telegram find-chat-id and inbound-status`.

### Task 10: Whole-suite check and draft PR

- [ ] **Step 1:** Run `cd plugin/engine && .venv/bin/python -m pytest tests -q`. Expected: all pass. Fix anything red before going on.
- [ ] **Step 2:** Check the new docs, phases and code (`docs/superpowers`, `plugin/phases/respond`, `plugin/engine/scout/telegram_inbound`) for personal names, home-directory paths, employer or client names. Expected: none (this repo is public).
- [ ] **Step 3:** Push the branch, then run `gh pr create --draft --repo Raven-Scout/Scout --title "feat: immediate response to Telegram messages"`. The body links the spec and the plan, lists the two trims, and gives the on-switch steps. It ends with the 🤖 Generated line.
