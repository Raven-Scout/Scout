- **Doctor warnings are ranked by risk, and the owner can mark one reviewed** (UI audit R13, #505;
  `engine/scout/doctor_triage.py`). `scoutctl bootstrap doctor --json` gains `warning_triage`: every
  warning with its `risk` (`high`, `medium` or `low`), the `reason`, its files, a `fingerprint`, and
  `reviewed_at` once the owner has reviewed it. `scoutctl bootstrap review-warning ID --fingerprint FP`
  (and `--undo`) records a review; the owner only, never a scheduled run (exit 2). A reviewed warning
  leaves `warnings`, `warning_groups` and the doctor's severity until what it is about changes: its files,
  a high-risk file's content, or its text. Reviews live in `~/.local/state/scout/reviewed-warnings.json`,
  a protected engine root that the write gate and the OS write sandbox keep runs from writing.
