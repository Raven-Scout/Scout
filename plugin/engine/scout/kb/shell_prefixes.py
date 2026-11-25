"""The words a shell command can start with that run the word after them: one table for both run gates.

scout.kb.raw_read (the KB read gate's command judge) and scout.kb.live_run (the run escape walk) each strip these
before the program they judge. They used to keep a list each, and the lists drifted (#823's review found `noglob`
and `nocorrect` in one only). Each now builds its own set from the groups here, and
tests/unit/test_shell_prefixes.py pins how they relate.

- :data:`MODIFIERS` run the next word as written, changing nothing a gate looks at: zsh's precommand modifiers
  (``noglob``, ``nocorrect``, ``-``, ``builtin``, ``command``, ``exec``) and ``sudo``, ``nice``, ``time``,
  ``xargs``. Both gates strip them.
- :data:`LAUNCHERS` run the next word after options and values of their own (``timeout 10``, ``stdbuf -oL``,
  ``xcrun --sdk x``, ``arch -arm64``). raw_read strips them with those values; live_run looks through them for
  git (its git wrappers) but leaves them in place elsewhere, since it strips words without their values.
- :data:`SELF_JUDGED` run the next word too, but what they do is judged on its own: ``env`` (``env -i`` and
  ``env -u`` change the run's environment), ``nohup`` (it detaches), and the shells and ``eval`` (their program
  is judged again). raw_read strips them to judge the command after them; live_run never does.

No imports: both modules load this at import time.
"""

from __future__ import annotations

MODIFIERS = frozenset({"sudo", "command", "builtin", "exec", "nice", "time", "xargs", "noglob", "nocorrect", "-"})
LAUNCHERS = frozenset(
    {"timeout", "gtimeout", "stdbuf", "gstdbuf", "caffeinate", "unbuffer", "parallel", "xcrun", "arch"}
)
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
SELF_JUDGED = frozenset({"env", "nohup", "eval"} | SHELLS)
# Every word raw_read strips before the program it judges.
ALL = MODIFIERS | LAUNCHERS | SELF_JUDGED
