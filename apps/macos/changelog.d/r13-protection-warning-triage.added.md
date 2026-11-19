- **Runs that ran without write protection are marked** (UI audit C9, part two). In the Control
  Center's run list, a run whose ledger row says write protection was off, unavailable or failed gets an
  "Unprotected" badge, and its run detail says why. A run whose post-run check found a protected file
  it had changed gets a red "File put back" badge, and a red "Scout put the protected file back" block in
  its detail naming the restoring commit, with "Show the change" opening it in the Diff tab (or, when
  Scout couldn't put it back, what to do). All of it comes from the run ledger, never from the log.
- **The Health page ranks Scout's engine warnings by risk, and you can mark one reviewed** (UI audit R13,
  #505). Each warning shows "High risk", "Medium risk" or "Low risk" and why, highest first. "Mark
  reviewed" hides a warning from every count until what it is about changes; reviewed warnings are listed
  under "Reviewed", with "Show again". Only you can do this: Scout keeps reviews outside the vault, where
  scheduled runs can't write.
