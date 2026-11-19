- **Doctor warnings are ranked by risk, and the owner can mark one reviewed** (UI audit R13, #505;
  `engine/scout/doctor_triage.py`). `scoutctl bootstrap doctor --json` gains `warning_triage`: every
  warning with its `risk` (`high`, `medium` or `low`), the `reason`, its files, a `fingerprint`,
  `reviewable`, and `reviewed_at` once the owner has reviewed it. `scoutctl bootstrap review-warning ID
  --fingerprint FP` (and `--undo`) records a review. It is the owner's only: it refuses a scheduled run by
  its environment and by the live-run process check (exit 2). A reviewed warning leaves `warnings`,
  `warning_groups` and the doctor's severity until its fingerprint changes: a file-backed warning's files
  or any byte of their content, any other warning's text. Warnings about run protection itself (the
  privacy check's blocked or unchecked counts, the gate probe, write protection, run limits, the repo-sync
  commit hook, an unreadable kb-lint config, a drift scan that couldn't run) are `reviewable: false`: the
  engine never lets a review hide them. A drift scan that fails no longer takes the privacy findings with it. Reviews live in
  `~/.local/state/scout/reviewed-warnings.json`, a protected engine root that the write gate and the OS
  write sandbox keep runs from writing.
