- **Run now is recorded as started by a person.** `scoutctl schedule fire-now` (Scout.app's Run now and Run
  again) now starts the run with `SCOUT_TRIGGER=manual`, so its run ledger row says `trigger: manual` with
  the schedule's own slot. It used to say `schedule`, the same as a run the tick started, so the app could
  never mark it "Started by you". The tick still records `schedule`.
- **A failed Run now of a schedule still counts against that schedule.** `runs.failed_today` decides whether
  a run belongs to a schedule from its slot, not its trigger. A forced run of `morning-briefing` that failed
  stays reported until `morning-briefing` next finishes a run, instead of clearing when any other schedule
  succeeds. A hand-started `manual` or `dreaming-manual` run, which no schedule runs, still clears on any
  later finished run.
