- **Control Center names each run the way the Health page does (UI audit T2).** A run's name now comes from
  its run ledger row's schedule (`slot`, or `mode` when the slot is unknown), not from its log's file name. A
  1 AM run the ledger records as `overnight-research` used to read "Morning briefing", because its log is a
  plain `scout-…` file and the hour alone made it a briefing. The log-name guess is still used when a run has
  no ledger row, for example while it is still going. The run list, the run detail, the menu-bar panel,
  status lines and notifications all use the new name. Health and Control Center now name hand-started runs
  the same way ("Dreaming (manual)", not "Dreaming manual"), and the "manual" badge is left off a run whose
  name already says so.
- **The run list is headed "Runs".** It was headed "Sessions", the name of the Agent Sessions page. Its
  search field, empty state, the page subtitle and the heatmap count ("238 runs · 1 mo") now say runs too.
- **The run list drops its commits and cost columns.** Both only ever showed dashes: commits load only when
  a run's detail opens, and runs rarely carry a cost. The run detail still shows both.
- **Selecting a run no longer renames the main window.** The window stays "Scout"; the detail header names
  the run.
