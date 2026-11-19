- **Runs that ran without write protection are marked** (UI audit C9, part two). In the Control
  Center's run list, a run whose ledger row says write protection was off, unavailable or failed gets an
  "Unprotected" badge, and its run detail says why. A run whose post-run check found a protected file
  it had changed gets a red "File put back" badge (both badges when both happened), and a red "Scout put
  the protected file back" block in its detail naming that run's own restoring commit, with "Show the
  change" opening it in the Diff tab (or, when Scout couldn't put it back, what to do). A run whose
  protected files couldn't be checked gets "Files not checked". All of it comes from the run ledger,
  never from the log.
- **The Health page ranks Scout's engine warnings by risk, and you can mark one reviewed** (UI audit R13,
  #505). Each warning shows "High risk", "Medium risk" or "Low risk" and why, highest first. "Mark
  reviewed" hides a warning from every count until its files, their content or its text change; reviewed
  warnings are listed under "Reviewed", with "Show again". Warnings about run protection itself (the
  privacy check's counts, write protection) have no "Mark reviewed": they always count. Only you can
  review: Scout keeps reviews outside the vault, where scheduled runs can't write.
