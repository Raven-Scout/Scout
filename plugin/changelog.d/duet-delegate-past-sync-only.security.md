- **A scheduled run can no longer turn on a delegate's `include_private` by respelling it** (`engine/scout/kb/lint_privacy.py`).
  The commit check compared the loaded `privacy:` block, but the flag counts only when written exactly `true`, so a
  run that rewrote an owner's `yes` (or `on`, `True`) to `true` changed nothing the check saw while the flag went
  from off to on. The check now also compares which delegates each flag is on for. It covers the new
  `ignore_sync_only` the same way.
