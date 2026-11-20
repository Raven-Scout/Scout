- **One schedule has one name across Control Center.** The Upcoming strip, the menu-bar Upcoming rows and the
  status bar's next run now name a default schedule from its slot key ("Midday consolidation", "Nightly
  dreaming"), as the Runs list does for its finished runs. They used to name it by its type ("Consolidation",
  "Dreaming"), which didn't tell the four consolidation schedules apart. A schedule you named like a
  hand-started run (`weekly-manual`) reads "Weekly manual" in both places.
- **The Runs list's Type filter files a run by its schedule.** A run's type there comes from its run ledger
  row's key (slot, or mode when the slot is unknown), not from its log's file name and hour. A schedule you
  made in the app, such as a 1 AM `overnight-research` slot of type research that runs `run-scout.sh`, is
  filed under Research instead of Morning briefing, using the slot's type from the schedule. When a run's
  slot is unknown and its mode names it, search matches the mode too.
- **The "Started by you" badge shows on a run you started with Run now.** A run's log never says who
  started it, so the badge never appeared. The run ledger now records Run now as `trigger: manual` (see the
  plugin's entry), and the run shows the badge, named after its schedule ("Evening consolidation"). A
  hand-started run whose name already says "(manual)" doesn't get it as well: the badge and the name read
  the same key.
