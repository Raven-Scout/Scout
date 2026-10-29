"""Thin entry for the per-tool-call privacy hooks: ``python -m scout.hooks <name>``.

``scoutctl hook <name>`` goes through ``scout.cli``, whose import (typer and
every subcommand) costs ~0.7 s, and these hooks run on tool calls: the
kb-read-label hook on every Read of a KB note, in the owner's sessions too.
``engine/bin/scoutctl`` sends ``hook kb-read-gate|kb-read-label|outbound-gate``
here instead, so hooks.json is unchanged. Nothing reachable from this module
may import typer or ``scout.cli`` (``tests/unit/test_hooks_entry.py``).

The ``scoutctl hook …`` commands stay as they are, for a direct call.
"""

from __future__ import annotations

import sys
from importlib import import_module

HOOKS = {
    "kb-read-gate": "scout.hooks.kb_read_gate",
    "kb-read-label": "scout.hooks.kb_read_label",
    "outbound-gate": "scout.hooks.outbound_gate",
}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in HOOKS:
        print(f"usage: python -m scout.hooks {{{','.join(HOOKS)}}}", file=sys.stderr)
        return 2
    hook_main = import_module(HOOKS[argv[0]]).main
    return int(hook_main())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
