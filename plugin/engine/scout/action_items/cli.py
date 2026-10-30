"""scoutctl action-items sub-app.

Top-level imports are intentionally minimal — Typer + stdlib + scout.errors.
Each subcommand imports its scout.action_items.* module inside the function
body so scoutctl startup latency is unaffected (Plan 1 perf rule, enforced
by tests/perf/test_no_heavy_imports.py).
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json as _json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from scout.errors import ActionItemError

if TYPE_CHECKING:
    from scout.events import Event

app = typer.Typer(help="Action-items operations.", no_args_is_help=True)


@app.command("mark-done")
def cli_mark_done(
    subject: str | None = typer.Option(None, "--subject", help="Substring of task title (legacy fallback)."),
    by_id: str | None = typer.Option(None, "--by-id", help="Stable [#TAG] id (2-8 [A-Z0-9], >=1 letter)."),
    undo: bool = typer.Option(False, "--undo", help="Reopen a completed task (flip [x]/[X] back to [ ])."),
    path: Path | None = typer.Argument(
        None,
        help="Daily markdown file (default: today). When given, its grandparent is the data dir.",
    ),
) -> None:
    from scout.action_items.mark_done import mark_done

    if (subject is None) == (by_id is None):
        raise ActionItemError("mark-done requires exactly one of --subject or --by-id")

    # Backward compat: if a path argument is given, its grandparent serves as
    # the data dir (path lives at <data_dir>/action-items/<file>.md). The
    # filename's date is used to pin which daily file to operate on.
    data_dir: Path | None = None
    date: _dt.date | None = None
    if path is not None:
        data_dir = path.parent.parent
        # Filename: action-items-YYYY-MM-DD.md
        stem = path.stem  # e.g. action-items-2026-04-15
        try:
            date = _dt.date.fromisoformat(stem.removeprefix("action-items-"))
        except ValueError as e:
            raise ActionItemError(f"unrecognized daily filename: {path.name}") from e

    mark_done(by_id=by_id, by_subject=subject, date=date, data_dir=data_dir, undo=undo)


@app.command("snooze")
def cli_snooze(
    until: str = typer.Option(..., "--until", help="YYYY-MM-DD"),
    subject: str | None = typer.Option(None, "--subject", help="Substring of task title (legacy fallback)."),
    by_id: str | None = typer.Option(None, "--by-id", help="Stable [#TAG] id (2-8 [A-Z0-9], >=1 letter)."),
    from_kind: str | None = typer.Option(
        None,
        "--from-kind",
        help=(
            "Source section kind (e.g. 'urgent', 'todo'). Recorded in the snoozed-until marker so a "
            "carry-forward can recover the original priority on the target day."
        ),
    ),
    path: Path | None = typer.Argument(
        None,
        help="Daily markdown file (default: today). When given, its grandparent is the data dir.",
    ),
) -> None:
    from scout.action_items.snooze import snooze

    if (subject is None) == (by_id is None):
        raise ActionItemError("snooze requires exactly one of --subject or --by-id")

    try:
        target_date = _dt.date.fromisoformat(until)
    except ValueError as e:
        raise ActionItemError(f"--until: invalid date {until!r}") from e

    # Backward compat: if a path argument is given, its grandparent serves as
    # the data dir (path lives at <data_dir>/action-items/<file>.md). The
    # filename's date is used to pin which daily file to operate on.
    data_dir: Path | None = None
    date: _dt.date | None = None
    if path is not None:
        data_dir = path.parent.parent
        stem = path.stem  # e.g. action-items-2026-04-15
        try:
            date = _dt.date.fromisoformat(stem.removeprefix("action-items-"))
        except ValueError as e:
            raise ActionItemError(f"unrecognized daily filename: {path.name}") from e

    snooze(
        by_id=by_id,
        by_subject=subject,
        from_kind=from_kind,
        until=target_date,
        date=date,
        data_dir=data_dir,
    )


@app.command("add-comment")
def cli_add_comment(
    comment: str = typer.Option(..., "--comment", help="Comment text to append beneath the task."),
    subject: str | None = typer.Option(None, "--subject", help="Substring of task title (legacy fallback)."),
    by_id: str | None = typer.Option(None, "--by-id", help="Stable [#TAG] id (2-8 [A-Z0-9], >=1 letter)."),
    author: str = typer.Option(
        "scout", "--author", help="Comment attribution (default: scout). GUI passes the signed-in user."
    ),
    path: Path | None = typer.Argument(
        None,
        help="Daily markdown file (default: today). When given, its grandparent is the data dir.",
    ),
) -> None:
    from scout.action_items.add_comment import add_comment

    if (subject is None) == (by_id is None):
        raise ActionItemError("add-comment requires exactly one of --subject or --by-id")

    # Backward compat: if a path argument is given, its grandparent serves as
    # the data dir (path lives at <data_dir>/action-items/<file>.md). The
    # filename's date is used to pin which daily file to operate on.
    data_dir: Path | None = None
    date: _dt.date | None = None
    if path is not None:
        data_dir = path.parent.parent
        stem = path.stem  # e.g. action-items-2026-04-15
        try:
            date = _dt.date.fromisoformat(stem.removeprefix("action-items-"))
        except ValueError as e:
            raise ActionItemError(f"unrecognized daily filename: {path.name}") from e

    add_comment(by_id=by_id, by_subject=subject, comment=comment, author=author, date=date, data_dir=data_dir)


@app.command("delete-comment")
def cli_delete_comment(
    subject: str | None = typer.Option(None, "--subject", help="Substring of task title (legacy fallback)."),
    by_id: str | None = typer.Option(None, "--by-id", help="Stable [#TAG] id (2-8 [A-Z0-9], >=1 letter)."),
    index: int | None = typer.Option(None, "--index", help="1-based index of the comment to delete."),
    text: str | None = typer.Option(None, "--text", help="Case-insensitive substring of the comment body."),
    path: Path | None = typer.Argument(
        None,
        help="Daily markdown file (default: today). When given, its grandparent is the data dir.",
    ),
) -> None:
    from scout.action_items.delete_comment import delete_comment

    if (subject is None) == (by_id is None):
        raise ActionItemError("delete-comment requires exactly one of --subject or --by-id")
    if (index is None) == (text is None):
        raise ActionItemError("delete-comment requires exactly one of --index or --text")

    data_dir: Path | None = None
    date: _dt.date | None = None
    if path is not None:
        data_dir = path.parent.parent
        stem = path.stem
        try:
            date = _dt.date.fromisoformat(stem.removeprefix("action-items-"))
        except ValueError as e:
            raise ActionItemError(f"unrecognized daily filename: {path.name}") from e

    delete_comment(
        by_id=by_id,
        by_subject=subject,
        index=index,
        text=text,
        date=date,
        data_dir=data_dir,
    )


@app.command("edit-comment")
def cli_edit_comment(
    new_text: str = typer.Option(..., "--new-text", help="Replacement body for the comment."),
    subject: str | None = typer.Option(None, "--subject", help="Substring of task title (legacy fallback)."),
    by_id: str | None = typer.Option(None, "--by-id", help="Stable [#TAG] id (2-8 [A-Z0-9], >=1 letter)."),
    index: int | None = typer.Option(None, "--index", help="1-based index of the comment to edit."),
    text: str | None = typer.Option(None, "--text", help="Case-insensitive substring of the comment body."),
    path: Path | None = typer.Argument(
        None,
        help="Daily markdown file (default: today). When given, its grandparent is the data dir.",
    ),
) -> None:
    from scout.action_items.edit_comment import edit_comment

    if (subject is None) == (by_id is None):
        raise ActionItemError("edit-comment requires exactly one of --subject or --by-id")
    if (index is None) == (text is None):
        raise ActionItemError("edit-comment requires exactly one of --index or --text")

    data_dir: Path | None = None
    date: _dt.date | None = None
    if path is not None:
        data_dir = path.parent.parent
        stem = path.stem
        try:
            date = _dt.date.fromisoformat(stem.removeprefix("action-items-"))
        except ValueError as e:
            raise ActionItemError(f"unrecognized daily filename: {path.name}") from e

    edit_comment(
        new_text=new_text,
        by_id=by_id,
        by_subject=subject,
        index=index,
        text=text,
        date=date,
        data_dir=data_dir,
    )


@app.command("render")
def cli_render(
    path: Path | None = typer.Argument(None),
) -> None:
    from scout import paths
    from scout.action_items.render import render

    target = path or paths.action_items_daily_path()
    sys.stdout.write(render(target))


def _daily_target(path: Path | None) -> tuple[Path | None, _dt.date | None]:
    """A daily-file path argument pins the data dir (its grandparent) and the date (its stem)."""
    if path is None:
        return None, None
    try:
        return path.parent.parent, _dt.date.fromisoformat(path.stem.removeprefix("action-items-"))
    except ValueError as e:
        raise ActionItemError(f"unrecognized daily filename: {path.name}") from e


def _require_one_selector(command: str, subject: str | None, by_id: str | None) -> None:
    if (subject is None) == (by_id is None):
        raise ActionItemError(f"{command} requires exactly one of --subject or --by-id")


def _duration_minutes(text: str, flag: str) -> int:
    from scout.planning.durations import parse_duration

    try:
        return parse_duration(text)
    except ValueError as e:
        raise ActionItemError(f"{flag}: invalid duration {text!r} (use e.g. 15m, 45m, 1h, 1h15m)") from e


_SUBJECT_HELP = "Substring of task title (legacy fallback)."
_BY_ID_HELP = "Stable [#TAG] id (2-8 [A-Z0-9], >=1 letter)."
_PATH_HELP = "Daily markdown file (default: today). When given, its grandparent is the data dir."


@app.command("set-estimate")
def cli_set_estimate(
    duration: str = typer.Argument(..., help="Planned time on the grid, e.g. 45m or 1h15m."),
    raw: str | None = typer.Option(None, "--raw", help="The estimate before the buffer or calibration factor."),
    kind: str | None = typer.Option(None, "--kind", help="Kind of work, e.g. deep, shallow, comms, review."),
    subject: str | None = typer.Option(None, "--subject", help=_SUBJECT_HELP),
    by_id: str | None = typer.Option(None, "--by-id", help=_BY_ID_HELP),
    path: Path | None = typer.Argument(None, help=_PATH_HELP),
) -> None:
    """Write or replace the `- estimate:` marker under a task (/scout-plan)."""
    from scout.action_items.plan_marks import set_estimate

    _require_one_selector("set-estimate", subject, by_id)
    minutes = _duration_minutes(duration, "duration")
    raw_minutes = _duration_minutes(raw, "--raw") if raw is not None else None
    data_dir, date = _daily_target(path)
    set_estimate(
        minutes=minutes,
        raw_minutes=raw_minutes,
        kind=kind,
        by_id=by_id,
        by_subject=subject,
        date=date,
        data_dir=data_dir,
    )


@app.command("set-block")
def cli_set_block(
    day: str = typer.Option(..., "--date", help="YYYY-MM-DD of the calendar block."),
    start: str = typer.Option(..., "--start", help="HH:MM, on the planning grid."),
    end: str = typer.Option(..., "--end", help="HH:MM, on the planning grid."),
    event_id: str | None = typer.Option(None, "--event-id", help="Calendar event id of the block."),
    subject: str | None = typer.Option(None, "--subject", help=_SUBJECT_HELP),
    by_id: str | None = typer.Option(None, "--by-id", help=_BY_ID_HELP),
    path: Path | None = typer.Argument(None, help=_PATH_HELP),
) -> None:
    """Write or replace the `- block:` marker under a task (/scout-plan)."""
    from scout.action_items.plan_marks import set_block

    _require_one_selector("set-block", subject, by_id)
    data_dir, date = _daily_target(path)
    set_block(
        day=day,
        start=start,
        end=end,
        event_id=event_id,
        by_id=by_id,
        by_subject=subject,
        date=date,
        data_dir=data_dir,
    )


@app.command("clear-block")
def cli_clear_block(
    subject: str | None = typer.Option(None, "--subject", help=_SUBJECT_HELP),
    by_id: str | None = typer.Option(None, "--by-id", help=_BY_ID_HELP),
    path: Path | None = typer.Argument(None, help=_PATH_HELP),
) -> None:
    """Remove the `- block:` marker from a task, if it has one."""
    from scout.action_items.plan_marks import clear_block

    _require_one_selector("clear-block", subject, by_id)
    data_dir, date = _daily_target(path)
    clear_block(by_id=by_id, by_subject=subject, date=date, data_dir=data_dir)


@app.command("clear-plan")
def cli_clear_plan(
    subject: str | None = typer.Option(None, "--subject", help=_SUBJECT_HELP),
    by_id: str | None = typer.Option(None, "--by-id", help=_BY_ID_HELP),
    path: Path | None = typer.Argument(None, help=_PATH_HELP),
) -> None:
    """Remove every plan marker (estimate, block, actual) from a task."""
    from scout.action_items.plan_marks import clear_plan

    _require_one_selector("clear-plan", subject, by_id)
    data_dir, date = _daily_target(path)
    clear_plan(by_id=by_id, by_subject=subject, date=date, data_dir=data_dir)


@app.command("set-actual")
def cli_set_actual(
    duration: str = typer.Argument(..., help="Time the task really took, on the grid, e.g. 1h."),
    on: str | None = typer.Option(None, "--on", help="YYYY-MM-DD the work happened (default: today)."),
    subject: str | None = typer.Option(None, "--subject", help=_SUBJECT_HELP),
    by_id: str | None = typer.Option(None, "--by-id", help=_BY_ID_HELP),
    path: Path | None = typer.Argument(None, help=_PATH_HELP),
) -> None:
    """Record the `- actual:` time and add the sample to the estimate calibration log."""
    from scout.action_items.plan_marks import set_actual

    _require_one_selector("set-actual", subject, by_id)
    minutes = _duration_minutes(duration, "duration")
    data_dir, date = _daily_target(path)
    set_actual(minutes=minutes, on=on, by_id=by_id, by_subject=subject, date=date, data_dir=data_dir)


@app.command("list")
def cli_list(
    path: Path | None = typer.Argument(None),
    include_done: bool = typer.Option(False, "--include-done"),
    priority: str | None = typer.Option(None, "--priority"),
    section: str | None = typer.Option(None, "--section"),
    json_out: bool = typer.Option(False, "--json"),
    with_plan: bool = typer.Option(False, "--with-plan", help="With --json: add each item's estimate/block/actual."),
) -> None:
    from scout import paths
    from scout.action_items.list import format_items, list_items

    if with_plan and not json_out:
        raise typer.BadParameter("--with-plan needs --json", param_hint="--with-plan")
    target = path or paths.action_items_daily_path()
    items = list_items(target, include_done=include_done, priority=priority, section=section)
    if json_out:
        payload: list[dict[str, Any]] = [
            {
                "title": i.title,
                "priority": i.priority,
                "status": i.status,
                "section": i.section,
                "short_prefix": i.short_prefix,
            }
            for i in items
        ]
        if with_plan:
            from scout.action_items.plan_marks import read_plan_marks

            for row, item in zip(payload, items, strict=True):
                row["plan"] = read_plan_marks(target, task_line_number=item.line_number).to_json_dict()
        sys.stdout.write(_json.dumps(payload) + "\n")
    else:
        sys.stdout.write(format_items(items))


@app.command("new-prefix")
def cli_new_prefix(
    path: Path | None = typer.Argument(
        None,
        help="Daily markdown file (default: today). Used to derive data_dir for the id-map.",
    ),
) -> None:
    """Print a fresh `[#XXXX]` Crockford prefix not currently in the id-map.

    Briefing/consolidation/dreaming prompts call this once per new task line
    so every task carries a stable identifier from creation. Output is bare
    (no `[#]` brackets, no newline-prefix) so callers can interpolate it
    directly into a task line:

        prefix=$(scoutctl action-items new-prefix)
        echo "- [ ] [#${prefix}] **subject** body" >> "$DAILY"

    Reads existing id-map.json and the topic tags in topics.md to avoid
    collision; does NOT register the new prefix — registration happens when
    the action-items writer next sees the line with the prefix attached.
    """
    from scout import paths
    from scout.action_items.topics import topic_tags
    from scout.id_map import IdMap
    from scout.ids import new_short_prefix

    data_dir = path.parent.parent if path is not None else paths.data_dir()
    id_map = IdMap.load(data_dir)
    prefix = new_short_prefix(exclude=id_map.in_use_prefixes() | topic_tags(data_dir))
    sys.stdout.write(prefix + "\n")


@app.command("materialize")
def cli_materialize(
    date: str | None = typer.Option(
        None, "--date", help="Target day YYYY-MM-DD (default: today in the configured timezone)."
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", help="Vault root (default: $SCOUT_DATA_DIR or ~/Scout)."),
) -> None:
    """Ensure today's daily file exists and is COMPLETE (daily-file completeness invariant).

    If the daily file is missing, copy the most recent prior daily file
    (up to 7 days back) verbatim under a fresh date header and a provisional
    banner, so no session or app surface ever renders a missing/stub list.
    Deterministic and idempotent — a no-op when the file already exists.
    Runner preambles call this before every session (Pattern #110 backstop).
    """
    from scout.action_items.materialize import materialize

    target_date: _dt.date | None = None
    if date is not None:
        try:
            target_date = _dt.date.fromisoformat(date)
        except ValueError as e:
            raise ActionItemError(f"--date: invalid date {date!r}") from e

    created = materialize(data_dir=data_dir, date=target_date)
    if created is None:
        sys.stdout.write("materialize: nothing to do (daily file exists or no prior file within 7 days)\n")
    else:
        sys.stdout.write(f"materialize: created {created}\n")


@app.command("archive-stale")
def cli_archive_stale(
    date: str | None = typer.Option(
        None, "--date", help="Day whose file to clean, YYYY-MM-DD (default: today in the configured timezone)."
    ),
    days: int | None = typer.Option(
        None, "--days", min=1, help="Archive items whose text is unchanged for this many days (default: 60)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would move; write nothing."),
    data_dir: Path | None = typer.Option(None, "--data-dir", help="Vault root (default: $SCOUT_DATA_DIR or ~/Scout)."),
) -> None:
    """Move open items nobody has touched for 60+ days into the monthly stale-items archive.

    `materialize` already does this once a day when it creates the day's
    file; run this to apply it to a file that already exists. Items move
    verbatim to `action-items/archive/stale-items-YYYY-MM.md` — nothing is
    deleted.
    """
    from scout import paths
    from scout.action_items.stale import STALE_AFTER_DAYS, archive_stale
    from scout.config import today

    vault = data_dir or paths.data_dir()
    if date is None:
        day = today(vault)
    else:
        try:
            day = _dt.date.fromisoformat(date)
        except ValueError as e:
            raise ActionItemError(f"--date: invalid date {date!r}") from e
    archived = archive_stale(data_dir=vault, date=day, max_age_days=days or STALE_AFTER_DAYS, dry_run=dry_run)
    if not archived:
        sys.stdout.write("archive-stale: nothing untouched long enough to archive\n")
        return
    verb = "would archive" if dry_run else "archived"
    noun = "item" if len(archived) == 1 else "items"
    sys.stdout.write(f"archive-stale: {verb} {len(archived)} {noun}:\n")
    for item in archived:
        sys.stdout.write(f"  [#{item.tag}] unchanged since {item.last_changed.isoformat()}\n")


@app.command("check-review-requests")
def cli_check_review_requests(
    date: str | None = typer.Option(
        None, "--date", help="Day whose file to check, YYYY-MM-DD (default: today in the configured timezone)."
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", help="Vault root (default: $SCOUT_DATA_DIR or ~/Scout)."),
    context: Path | None = typer.Option(
        None, "--context", help="Pre-fetched context (default: <vault>/.scout-cache/session-context.json)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """List pre-fetched review requests today's action items don't account for.

    A request is accounted for by a task naming its `owner/repo#N` or PR URL, a
    `- Skipped review request <ref> — <reason>` line, or a dismissed URL. Run it
    before committing a briefing or consolidation. Exit codes: 0 = all
    accounted for, 1 = some listed (resolve each), 2 = cannot check.
    """
    from scout.action_items.review_requests import SKIP_LINE_FORMAT, ReviewCheckError, check_review_requests
    from scout.config import today

    if date is not None:
        try:
            _dt.date.fromisoformat(date)
        except ValueError:
            sys.stdout.write(f"check-review-requests: --date: invalid date {date!r}\n")
            raise typer.Exit(code=2) from None
    day = date or today(data_dir).isoformat()
    try:
        result = check_review_requests(data_dir=data_dir, today=day, context=context)
    except ReviewCheckError as e:
        if json_out:
            sys.stdout.write(_json.dumps({"error": str(e)}) + "\n")
        else:
            sys.stdout.write(f"check-review-requests: cannot check — {e}\n")
        raise typer.Exit(code=2) from e

    if json_out:
        rows = [{"ref": r.ref, "url": r.url, "title": r.title} for r in result.unaccounted]
        sys.stdout.write(_json.dumps({"checked": len(result.checked), "unaccounted": rows}) + "\n")
    elif not result.checked:
        sys.stdout.write("check-review-requests: no review requests were pre-fetched\n")
    elif not result.unaccounted:
        n = len(result.checked)
        noun = "request is" if n == 1 else "requests are"
        sys.stdout.write(f"check-review-requests: all {n} pre-fetched review {noun} accounted for\n")
    else:
        sys.stdout.write(
            f"check-review-requests: {len(result.unaccounted)} of {len(result.checked)} pre-fetched review "
            "requests are not in today's action items:\n"
        )
        for r in result.unaccounted:
            sys.stdout.write(f"  {r.ref} — {r.title} — {r.url}\n")
        sys.stdout.write(
            f"Classify each with `gh pr view` and place it, or add a run-notes line: `{SKIP_LINE_FORMAT}`.\n"
        )
    if result.unaccounted:
        raise typer.Exit(code=1)


def _check_day(name: str, date: str | None, data_dir: Path | None) -> str:
    from scout.config import today

    if date is not None:
        try:
            _dt.date.fromisoformat(date)
        except ValueError:
            sys.stdout.write(f"{name}: --date: invalid date {date!r}\n")
            raise typer.Exit(code=2) from None
    return date or today(data_dir).isoformat()


def _cannot_check(name: str, error: Exception, json_out: bool) -> typer.Exit:
    if json_out:
        sys.stdout.write(_json.dumps({"error": str(error)}) + "\n")
    else:
        sys.stdout.write(f"{name}: cannot check — {error}\n")
    return typer.Exit(code=2)


@app.command("check-linear-delta")
def cli_check_linear_delta(
    date: str | None = typer.Option(
        None, "--date", help="Day whose file to check, YYYY-MM-DD (default: today in the configured timezone)."
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", help="Vault root (default: $SCOUT_DATA_DIR or ~/Scout)."),
    context: Path | None = typer.Option(
        None, "--context", help="Pre-fetched context (default: <vault>/.scout-cache/session-context.json)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Check today's 📋 Scout Digest reports your own Linear activity since the last run.

    With a Linear API key (`~/.scout-secrets/linear-api-key`) the runner
    pre-fetches that activity, and every pre-fetched issue must be named under
    `### Your Linear Activity` on a line this run wrote. Without one, this run
    must add a bullet under that heading, built from the Linear connector. A
    line already in the file before the session doesn't count. Run it before committing
    a briefing or consolidation. Exit codes: 0 = reported, 1 = not yet, 2 =
    cannot check.
    """
    from scout.action_items.linear_delta import HEADING, check_linear_delta
    from scout.action_items.prefetched import PrefetchCheckError

    name = "check-linear-delta"
    day = _check_day(name, date, data_dir)
    try:
        result = check_linear_delta(data_dir=data_dir, today=day, context=context)
    except PrefetchCheckError as e:
        raise _cannot_check(name, e, json_out) from e

    since = result.since or "the last run"
    if json_out:
        rows = [{"identifier": i.identifier, "title": i.title, "actions": list(i.actions)} for i in result.unreported]
        sys.stdout.write(
            _json.dumps(
                {
                    "status": result.status,
                    "since": result.since,
                    "prefetched": None if result.prefetched is None else len(result.prefetched),
                    "reported": result.reported,
                    "unreported": rows,
                    "truncated": result.truncated,
                    "passed": result.passed,
                }
            )
            + "\n"
        )
    elif result.prefetched == ():
        sys.stdout.write(f"{name}: no own Linear activity since {since} was pre-fetched\n")
    elif result.prefetched is not None and result.passed:
        n = len(result.prefetched)
        noun = "issue is" if n == 1 else "issues are"
        sys.stdout.write(f"{name}: all {n} pre-fetched Linear {noun} under ### {HEADING}\n")
    elif result.prefetched is not None:
        sys.stdout.write(
            f"{name}: {len(result.unreported)} of {len(result.prefetched)} issues you acted on in Linear since "
            f"{since} are not under ### {HEADING} in today's 📋 Scout Digest:\n"
        )
        for i in result.unreported:
            state = f" (now {i.state})" if i.state else ""
            sys.stdout.write(f"  {i.identifier} — {i.title} — {', '.join(i.actions)}{state}\n")
        sys.stdout.write(
            "Add a bullet for each: the identifier, what you did, and where it stands now. A line that was "
            "already there before this run doesn't count: update it with this run's action.\n"
        )
    elif result.passed:
        sys.stdout.write(f"{name}: reported under ### {HEADING} (not pre-fetched: {result.status or 'unknown'})\n")
    else:
        sys.stdout.write(
            f"{name}: your Linear activity was not pre-fetched ({result.status or 'unknown'}). Query the Linear "
            f"connector for the issues you created, moved, commented on or edited since {since}, and list each "
            f"under ### {HEADING} in today's 📋 Scout Digest. If there were none, write `- None since {since}`; "
            "if Linear could not be read, write `- Linear unavailable: <error>`. Bullets already there before "
            "this run don't count.\n"
        )
    if result.truncated and not json_out:
        sys.stdout.write(
            "  note: the pre-fetch hit a limit (linear_own_activity_truncated); check the Linear connector for "
            f"anything else you did since {since}.\n"
        )
    if not result.passed:
        raise typer.Exit(code=1)


@app.command("check-red-polls")
def cli_check_red_polls(
    date: str | None = typer.Option(
        None, "--date", help="Day whose file to check, YYYY-MM-DD (default: today in the configured timezone)."
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", help="Vault root (default: $SCOUT_DATA_DIR or ~/Scout)."),
    context: Path | None = typer.Option(
        None, "--context", help="Pre-fetched context (default: <vault>/.scout-cache/session-context.json)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """List the 🔴 project Slack channels today's file doesn't say were polled.

    The runner pre-fetches every 🔴 project's `slack_channels:`; each must
    appear on a `🔴 channels polled: …` line (by `#name` or id) on this run's
    🪵 Run notes entry: one that wasn't in the file before the session, so an
    earlier run's line doesn't count. Run it before committing a briefing or
    consolidation. Exit codes: 0 = all listed, 1 = some missing, 2 = cannot check.
    """
    from scout.action_items.prefetched import PrefetchCheckError
    from scout.action_items.red_polls import POLLED_LINE_FORMAT, check_red_polls

    name = "check-red-polls"
    day = _check_day(name, date, data_dir)
    try:
        result = check_red_polls(data_dir=data_dir, today=day, context=context)
    except PrefetchCheckError as e:
        raise _cannot_check(name, e, json_out) from e

    if json_out:
        rows = [{"project": c.project, "id": c.id, "name": c.name} for c in result.unpolled]
        sys.stdout.write(
            _json.dumps(
                {
                    "checked": len(result.channels),
                    "unpolled": rows,
                    "unconfigured": list(result.unconfigured),
                    "has_entry": result.has_entry,
                }
            )
            + "\n"
        )
    else:
        if not result.channels:
            sys.stdout.write(f"{name}: no 🔴 project declares a Slack channel\n")
        elif not result.unpolled:
            n = len(result.channels)
            sys.stdout.write(f"{name}: all {n} 🔴 project channel{'s' if n != 1 else ''} listed as polled\n")
        else:
            where = (
                "on this run's 🪵 Run notes entry"
                if result.has_entry
                else "(today's 🪵 Run notes has no entry from this run yet)"
            )
            sys.stdout.write(
                f"{name}: {len(result.unpolled)} of {len(result.channels)} 🔴 project channels are not listed "
                f"as polled {where}:\n"
            )
            for c in result.unpolled:
                sys.stdout.write(f"  {c.label} — {c.project}\n")
            sys.stdout.write(
                "Read each with `slack_read_channel` since the last run, then list it on this run's 🪵 Run notes "
                f"entry: `{POLLED_LINE_FORMAT}` (add `(unavailable: <error>)` after one you could not read).\n"
            )
        for project in result.unconfigured:
            sys.stdout.write(f"  note: 🔴 project {project} has no slack_channels: frontmatter; add it\n")
    if result.unpolled:
        raise typer.Exit(code=1)


@app.command("backfill-prefixes")
def cli_backfill_prefixes(
    path: Path | None = typer.Argument(
        None,
        help="Daily markdown file (default: today). When given, its grandparent is the data dir.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the would-be changes; don't write."),
) -> None:
    """Add `[#XXXX]` prefixes to every open task line that doesn't have one.

    Idempotent: lines that already carry a prefix are left untouched. New
    prefixes are minted via `scout.ids.new_short_prefix` (collision-checked
    against `id-map.json`) and registered into the id-map as part of the
    write. Lets existing vaults adopt the prefix convention without a
    hand-edit pass.
    """
    from scout import paths
    from scout.action_items.backfill import backfill_prefixes

    target = path or paths.action_items_daily_path()
    data_dir = path.parent.parent if path is not None else paths.data_dir()
    added = backfill_prefixes(target=target, data_dir=data_dir, dry_run=dry_run)
    if not added:
        sys.stdout.write("no unprefixed open tasks found\n")
        return
    verb = "would add" if dry_run else "added"
    sys.stdout.write(f"{verb} {len(added)} prefix(es):\n")
    for line_no, prefix, title in added:
        sys.stdout.write(f"  line {line_no}: [#{prefix}] {title}\n")


@app.command("backfill-context")
def cli_backfill_context(
    snapshot: Path = typer.Option(..., "--from", help="Pre-restructure snapshot of a daily file."),
    daily: Path | None = typer.Option(
        None, "--daily", help="Daily file (default: today). Its grandparent is the data dir."
    ),
    write: bool = typer.Option(False, "--write", help="Write the notes. Without it: dry run."),
) -> None:
    """Restore each open item's context note from a pre-restructure snapshot.

    Writes `action-items/context/<TAG>.md` for every open item in the daily
    file and `action-items/backlog.md` that has no note yet, copying its
    snapshot block verbatim. Never overwrites a note; re-running is a no-op.
    """
    from scout import paths
    from scout.action_items.context_notes import backfill_context

    if not snapshot.exists():
        sys.stderr.write(f"snapshot not found: {snapshot}\n")
        raise typer.Exit(code=2)
    target = daily or paths.action_items_daily_path()
    if not target.exists():
        sys.stderr.write(f"daily file not found: {target}\n")
        raise typer.Exit(code=2)
    data_dir = daily.parent.parent if daily is not None else paths.data_dir()
    report = backfill_context(data_dir=data_dir, snapshot=snapshot, daily=target, write=write)
    verb = "wrote" if write else "would write"
    sys.stdout.write(f"{verb} {len(report.to_write)} note(s); {len(report.existing)} already had one\n")
    if report.missing:
        sys.stdout.write(f"no snapshot entry: {', '.join(report.missing)}\n")
    if report.ambiguous:
        sys.stdout.write(f"ambiguous (several snapshot rows, none with this title): {', '.join(report.ambiguous)}\n")
    if report.over_budget:
        sys.stdout.write("over budget (written in full) — add to scout-config.yaml kb_budgets:\n")
        for tag, size in report.over_budget:
            sys.stdout.write(f"  'action-items/context/{tag}.md': null   # {size} bytes\n")
    if report.long_lines:
        sys.stdout.write(
            f"{len(report.long_lines)} note(s) have line(s) over the kb-lint limit (blocking `mega-line` "
            "in block mode) — commit the backfill with SCOUT_LINT_OVERRIDE='restore pre-restructure context'\n"
        )


@app.command("watch")
def cli_watch(
    target: str | None = typer.Argument(
        None,
        metavar="[DATE_OR_PATH]",
        help="YYYY-MM-DD for that day's file, an explicit path, or omit for today.",
    ),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI color (auto when stdout is not a TTY)."),
) -> None:
    """Stream changes to today's action items as they happen."""
    import re

    from scout import paths
    from scout.action_items.watch import run_watch_loop

    if target is None:
        target_path = paths.action_items_daily_path()
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", target):
        target_path = paths.action_items_daily_path(date=_dt.date.fromisoformat(target))
    else:
        target_path = Path(target).expanduser().resolve()

    if not target_path.exists():
        raise ActionItemError(f"target does not exist: {target_path}")

    color = not no_color and sys.stdout.isatty()
    run_watch_loop(target_path, color=color)


_DATA_DIR_HELP = "Vault root (default: $SCOUT_DATA_DIR or ~/Scout)."


@app.command("rename-topic")
def cli_rename_topic(
    tag: str = typer.Argument(..., help="The topic's [#TAG] in action-items/topics.md."),
    name: str = typer.Option(..., "--name", help="Your name for the topic; empty clears it."),
    data_dir: Path | None = typer.Option(None, "--data-dir", help=_DATA_DIR_HELP),
) -> None:
    """Set your own name for a topic, kept in action-items/topic-names.md. Scout runs never change it."""
    from scout.action_items.topic_names import rename_topic

    _print_event(rename_topic(tag.removeprefix("#"), name, data_dir=data_dir))


@app.command("dismiss")
def cli_dismiss(
    key: str = typer.Option(..., "--key", help="The entry's URL, or its text when it has none."),
    data_dir: Path | None = typer.Option(None, "--data-dir", help=_DATA_DIR_HELP),
) -> None:
    """Close an Inbox or Loose ends entry for good."""
    from scout.action_items.dismissed import dismiss

    _print_event(dismiss(key, data_dir=data_dir))


def _print_event(event: Event) -> None:
    sys.stdout.write(_json.dumps(dataclasses.asdict(event)) + "\n")
