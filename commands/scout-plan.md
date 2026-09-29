---
name: scout-plan
description: Interactive day planning. Logs how long yesterday's planned blocks really took, estimates today's open tasks with a calibrated buffer, packs them into free calendar time on a 15-minute grid, and creates the calendar blocks only after explicit approval. Runs in the current conversation.
---

# Scout Plan

Plan the user's day in conversation: pick the tasks that need their own time, estimate each one, fit them into the free time on their calendar, and, once they say yes, put the blocks on Google Calendar and record the estimate and block on each task.

Like `/scout-work`, this runs **in the current conversation**, and the user decides. **Never create, move or delete a calendar event without an explicit yes for that exact plan.**

Arguments (`$ARGUMENTS`):
- empty: plan today
- `tomorrow`, or a date `YYYY-MM-DD`: plan that day
- `review`: only log actual times for past blocks (Phase 1), then stop

---

## Phase 0: Setup

1. **Vault and dates.** The vault is the directory holding `scout-config.yaml` (the current directory, else `~/Scout`). Read its `timezone` (default `America/New_York`) and compute today and the target day with `TZ=<timezone> date '+%Y-%m-%d'`. Tasks always live in **today's** file `<vault>/action-items/action-items-<today>.md`, even when planning tomorrow; the next briefing carries them, and their plan markers, forward.
2. **Engine.** Resolve scoutctl and check that it knows planning:
   ```bash
   SCOUTCTL="${CLAUDE_PLUGIN_ROOT:-}/.venv/bin/scoutctl"
   [ -x "$SCOUTCTL" ] || SCOUTCTL="$(command -v scoutctl)"
   "$SCOUTCTL" manifest show | grep -q '"planning_v1": true' && echo PLANNING_OK || echo PLANNING_UNAVAILABLE
   ```
   On `PLANNING_UNAVAILABLE`, tell the user the installed engine predates `/scout-plan` and to run `/scout-update`, then stop.
3. **Settings and calibration.**
   ```bash
   "$SCOUTCTL" planning show --json
   "$SCOUTCTL" planning calibration --json
   "$SCOUTCTL" action-items list <today's file> --json --with-plan
   ```
   Use these values everywhere below: `work_start`, `work_end`, `increment_minutes` (the grid, 15 by default), `capacity_pct`, `buffer`, `new_work_buffer`, `deep_block_minutes`, `batch_block_minutes`, `meeting_gap_minutes`, `event_title_prefix`, `event_visibility`, `event_availability`. Never hardcode them.
4. **Calendar tools.** Use the Google Calendar MCP: `list_events`, `create_event`, `delete_event`. If they are not loaded, find them with ToolSearch. If there is no calendar connector, say so and offer a plan without calendar blocks.

---

## Phase 1: Log actual time for past blocks (the feedback loop)

This is how Scout learns how long work really takes. Do it first, every time.

1. From the `--with-plan` listing, collect open or done items whose `block.date` is **before today** and whose `actual_minutes` is null.
2. If there are none, say so in one line and go on (or stop, for `review`).
3. Ask about all of them in **one** message, as a numbered list: the task title, the block (`Tue 10:00-10:45`), the estimate, and the question "done? how long did it really take?". Answers are on the grid: 15m, 30m, 45m, 1h, 1h15m. Accept quick replies like "1: 30m, 2: not done, 3: 1h".
4. For each task the user says is finished, record it against the block's day:
   ```bash
   "$SCOUTCTL" action-items set-actual <duration> --on <block date> --by-id <TAG> <today's file>
   ```
   If a stated time is off the grid, round it up and say so.
5. For a task that is not finished: do not record an actual. Ask whether to plan it again today; if yes it becomes a candidate below and its old block marker is replaced when the new block is set. If the user drops it, run `clear-block` for it.
6. Show the effect in one line from a fresh `planning calibration --json`, e.g. "Deep work is now calibrated at 1.4x from 6 samples."
7. For `review`, commit (Phase 6) and stop here.

---

## Phase 2: Read the day

1. `list_events` for the target day from `work_start` to `work_end` in the user's timezone.
2. Busy time is every timed event the user has not declined, including their own focus-time and out-of-office events. All-day events do not block time unless they are out-of-office. If the whole day is out-of-office, say so and stop.
3. Events whose title starts with `event_title_prefix` and whose description has a `[scout:TAG]` marker are **earlier Scout blocks**. Keep them as busy, and treat their tasks as already planned: list them, do not plan them twice.
4. **Free windows:** the work window minus busy time, minus `meeting_gap_minutes` before and after every busy event, with every edge rounded **inward** to the grid. Drop windows shorter than one grid step. On today, start no earlier than the next grid step after now.
5. **Capacity:** `capacity_pct` percent of the total free minutes, rounded down to the grid. The rest stays unplanned on purpose, for messages, interruptions and things that come up.

---

## Phase 3: Pick the candidates

From today's file take open (`- [ ]`) items in 🔴 Urgent, 🟡 To Do and 💡 focus sections. Skip:
- 🟢 Watching items, and anything whose body says it is waiting on someone else, blocked, or "no action needed";
- items with a future `snoozed-until`;
- items that already have a `block` on the target day (Phase 2, step 3);
- personal items, unless the user asks for them.

Rank: overdue or due on the target day first, then 🔴, then 🟡 and 💡 by nearest deadline, then oldest. Work with the top 12 at most and say how many more exist.

---

## Phase 4: Estimate

For each candidate:
1. **Kind:** `deep` (focused making or thinking work), `shallow` (small admin, a quick fix), `comms` (replies, a message to write, a follow-up), or `review` (read and respond to someone else's work).
2. **Raw estimate:** your honest guess of the pure working time, in grid steps, from what the task actually involves. Give a one-sentence reason ("a 2-page draft from notes that exist").
3. **Factor:** the calibration `factor` for that kind when its `source` is `calibrated`; otherwise `buffer`. For a kind of work the user has not done before, use the larger of that and `new_work_buffer`.
4. **Planned:** raw x factor, rounded **up** to the grid. Never below one grid step.
5. **Split:** planned time above 2h becomes parts of at most `deep_block_minutes`, planned as separate blocks.

When calibration exists, say so once in the plan: "Deep work runs 1.4x your first guess (6 samples), I planned with that."

---

## Phase 5: Build and agree the plan

1. **Pack in rank order until capacity is used:**
   - `deep` tasks go into the **longest** free windows first, one task per block, each block at most `deep_block_minutes`;
   - `shallow`, `comms` and `review` tasks of 30 minutes or less are grouped into shared batch blocks of up to `batch_block_minutes`;
   - every block starts and ends on the grid, inside a free window.
2. **Present** one table and three lines:

   | Time | Task | Kind | Estimate | Why |
   |---|---|---|---|---|
   | 09:30-11:00 | Draft the rollout note `[#PROJ1]` | deep | 1h30m (raw 1h x 1.5) | 2-page draft, numbers still missing |

   - Capacity: "4h15m planned of 6h30m free (65% cap)."
   - Does not fit: the tasks left over, each with "suggest tomorrow" or "suggest dropping".
   - Question: "Create these N blocks, or change something?"
3. **Iterate.** The user may change estimates, times, order, kinds, or which tasks are in. Re-pack and show the table again. Every change stays on the grid.
4. Continue only on an explicit yes to the current table. "Looks good" about one row is not a yes for all of them.

---

## Phase 6: Create, record, commit

Only after the yes:

1. **One event per block** with `create_event`:
   - `summary`: `<event_title_prefix> <plain task title>`; a batch block uses `<prefix> Batch: <short list>`;
   - `description`: one `[scout:<TAG>]` marker per task in the block, then one sentence from each task;
   - `startTime` and `endTime` as ISO 8601 with the user's `timeZone`;
   - `availability`: `AVAILABILITY_BUSY` or `AVAILABILITY_FREE` from `event_availability`; `visibility` from `event_visibility`; `notificationLevel`: `NONE`; no attendees, no Meet link.
2. After **each** event is created, record the task side. Tasks in a batch block all get the same event id:
   ```bash
   "$SCOUTCTL" action-items set-estimate <planned> --raw <raw> --kind <kind> --by-id <TAG> <today's file>
   "$SCOUTCTL" action-items set-block --date <target day> --start <HH:MM> --end <HH:MM> --event-id <event id> --by-id <TAG> <today's file>
   ```
   If `create_event` fails, do not write that block's markers; report it and continue with the rest.
3. **Commit** the vault:
   ```bash
   git -C <vault> add action-items/ .scout-state/planning-log.jsonl
   git -C <vault> commit -m "plan [HH:MM]: <N> blocks for <target day>"
   ```
4. **Summary:** the blocks created with their times, anything that failed, and what was left for another day.

---

## Re-planning and cleanup

- **Moving a block:** a task that already has a block for the target day is shown, not replanned silently. To move it, with an explicit yes: `delete_event` the old event, create the new one, then `set-block` with the new id.
- **Removing a block:** with an explicit yes, `delete_event` it and run `clear-block` for its tasks.
- **Only ever touch events Scout created:** the title starts with `event_title_prefix` **and** the description carries a `[scout:TAG]` marker. Never edit or delete anything else on the calendar.

## Important Notes

- **Nothing reaches the calendar without an explicit yes.** Everything before Phase 6 is a proposal.
- **Everything sits on the grid:** estimates, block edges, actual times. Round up, never down, and say when you rounded.
- **Be honest in estimates.** A plan that fits on paper and fails in practice is worse than a shorter plan. When in doubt, estimate the larger step and leave the task for tomorrow.
- **Actual times are the user's word.** Never infer them from the calendar or from when a task was checked off.
- **The markers are machine data.** Write them only through `scoutctl action-items set-estimate | set-block | set-actual | clear-block`, never by editing the file.
- **Commit format:** `plan [HH:MM]: <summary>`, consistent with `work [HH:MM]:` and the session types.
