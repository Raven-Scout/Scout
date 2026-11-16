"""PreToolUse hook on Write/Edit/MultiEdit/NotebookEdit: a run can't write what decides which checks run.

The decision is :func:`scout.hooks.kb_read_gate.decide` (file tools are judged against
:mod:`scout.kb.hook_paths`). It has its own hook name so that an engine older than the
write gate, which knows only ``kb-read-gate``, fails closed on it: ``scoutctl hook
kb-write-gate`` is an unknown command there, which exits 2, and Claude Code blocks the
call (review of #637). Under the old name an old engine's ``decide()`` returned None for
every write tool, which allowed it.
"""

from __future__ import annotations

from scout.hooks.kb_read_gate import main

__all__ = ["main"]
