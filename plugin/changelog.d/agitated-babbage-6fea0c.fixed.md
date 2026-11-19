- **The post-run integrity check no longer blames a scheduled run for a vault worktree session's own commit**
  (`engine/scout/sandbox/integrity.py`). Linked worktrees share the vault's branches, so a session working in
  `.claude/worktrees/<name>` that committed a protected file (say `.claude/settings.local.json`) on its own branch
  while a run was going had the change "restored" onto its live branch, and run health went red
  (`run.integrity_restored`). A moved branch is now left alone, with one `integrity check:` log line, when a linked
  worktree that existed at the snapshot still has it checked out and that worktree's own index holds exactly the
  branch tip's version of every changed protected file. A sandboxed run can move any branch but can't write another
  worktree's HEAD or index, so a run that plants a change on a session's branch is still put back, and so is a
  session started after the run began.
