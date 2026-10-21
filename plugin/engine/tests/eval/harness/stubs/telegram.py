"""A fake ``scoutctl notify telegram`` for eval sessions (#453, spec 2026-10-20 §11.3).

``tests/eval/stubs/bin/scoutctl`` sends ``notify telegram`` here and every other
subcommand to the real ``scoutctl``. The message is recorded in the calls log
(persona ``telegram``, capture ``telegram``) and never sent: no eval run may
reach a real chat, and the privacy check reads the payload back from the log.

Environment: ``SCOUT_EVAL_CALLS``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .calls_log import CallsLog
from .rules import Reply


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="scoutctl notify telegram")
    p.add_argument("--tier", default="info")
    p.add_argument("--body")
    p.add_argument("--body-file", type=Path)
    p.add_argument("--parse-mode", default="none")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--allow-duplicate", action="store_true")
    a, _unknown = p.parse_known_args(argv)
    if (a.body is None) == (a.body_file is None):
        print("scoutctl notify telegram: pass exactly one of --body or --body-file", file=sys.stderr)
        return 2
    try:
        body = a.body if a.body is not None else a.body_file.read_text(encoding="utf-8")
    except OSError as e:
        print(f"scoutctl notify telegram: could not read --body-file {a.body_file}: {e}", file=sys.stderr)
        return 2
    log = CallsLog(Path(os.environ["SCOUT_EVAL_CALLS"]))
    args = {"tier": a.tier, "parse_mode": a.parse_mode, "body": body}
    log.record(persona="telegram", tool="notify", args=args, decide=lambda _hits: Reply("ok", None, None, "telegram"))
    print(f"id=eval-{len(log.rows())}", file=sys.stderr)  # the real command's receipt line
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
