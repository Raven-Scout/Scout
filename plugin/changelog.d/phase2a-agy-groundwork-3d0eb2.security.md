- **A scheduled run's shell command that names an `ANTIGRAVITY_*` variable is denied** (runtime-agnostic
  Phase 2A; `engine/scout/hooks/kb_read_gate.py`, `engine/scout/kb/hook_paths.py`). Antigravity CLI gives every
  shell child the address and a token of its local server, plus helper variables. Until a reviewed change shows
  that server can't act around Scout's hooks, the read gate denies a run's command that names any of them, in
  any case and on any runtime. No Claude Code run has these variables. None of the last 50 scheduled runs'
  1,735 shell calls named one.
  - This is a speed bump, not a boundary. It matches the command's text, so a command that builds the name at
    run time, or prints the environment once and then uses the value it saw, gets past it. No Antigravity run
    that calls a real model may start until a separate isolation design is approved (plan decision 11).
