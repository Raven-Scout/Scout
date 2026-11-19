"""scoutctl wishlist sub-app: checks the wishlist phase runs before it commits.

Top-level imports stay minimal (Typer + stdlib); each command imports its
module inside the function body so scoutctl startup is unaffected
(tests/perf/test_no_heavy_imports.py).
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import typer

app = typer.Typer(help="Checks the wishlist phase runs before it commits.", no_args_is_help=True)


@app.callback()
def _group() -> None:
    """Checks the wishlist phase runs before it commits."""
    # A callback keeps this a command group: a Typer app with one command and no callback collapses into that
    # command, and `check-log-move` would be read as its first PATH.


@app.command("check-log-move")
def cli_check_log_move(
    paths: list[str] | None = typer.Argument(
        None,
        help="The paths this commit will hold, as given to git-safe-commit.sh (default: every changed card, "
        "with progress notes as on disk).",
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", help="Vault root (default: $SCOUT_DATA_DIR or ~/Scout)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Check that a log leaving a wishlist card lands verbatim in its progress note, in the same commit.

    For each card (`docs/wishlist/<item>.md`) changed since the last commit,
    every dated line it lost (a line under a dated heading, a line that starts
    with a date, or a ticked sub-task with a date) must be in
    `docs/wishlist/progress/<item>.md` as this commit will hold it: byte for
    byte, in the card's order. A progress note not among PATHS counts as last
    committed. Run it before committing a wishlist change, with the same paths.
    Exit codes: 0 = every removed log line was moved, 1 = some were not, 2 = cannot check.
    """
    from scout.wishlist.log_move import LogMoveCheckError, check_log_move

    name = "check-log-move"
    try:
        result = check_log_move(data_dir=data_dir, paths=paths or None)
    except LogMoveCheckError as e:
        if json_out:
            sys.stdout.write(_json.dumps({"error": str(e)}) + "\n")
        else:
            sys.stdout.write(f"{name}: cannot check — {e}\n")
        raise typer.Exit(code=2) from e

    if json_out:
        sys.stdout.write(
            _json.dumps(
                {
                    "checked": result.checked,
                    "moved": result.moved,
                    "findings": [
                        {"item": f.item, "note": f.note, "missing": f.missing, "removed": f.removed}
                        for f in result.findings
                    ],
                    "passed": result.passed,
                }
            )
            + "\n"
        )
    elif not result.passed:
        n = len(result.findings)
        sys.stdout.write(f"{name}: {n} card{'s' if n != 1 else ''} lost log lines their progress note doesn't hold:\n")
        for f in result.findings:
            sys.stdout.write(f"  {f.item}: first missing line, in {f.note}:\n    {f.missing}\n")
        sys.stdout.write(
            "Copy each card's removed dated lines into its progress note exactly as they were on the card "
            "(same characters, same order; new entries may go above them), or put them back on the card. "
            "Commit the note with the card. Then run this again.\n"
        )
    else:
        n = len(result.checked)
        moved = f"; {result.moved} dated line(s) moved to progress notes" if result.moved else ""
        sys.stdout.write(f"{name}: {n} changed card(s), no log line lost{moved}\n")
    if not result.passed:
        raise typer.Exit(code=1)
