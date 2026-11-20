- **Settings and the Doctor window no longer call a problem healthy (UI audit T4).** Settings ▸ Engine showed
  "1 problem · 3 warnings" labelled "Healthy, with warnings", because it checked the install's verdict
  before the runs'. Its Health row now takes the worse of the two, in the Health page's words and colours:
  "Needs fixing" in red, "Needs a look" in amber, "Healthy" in green. A failing Runs row is red, not amber.
  The Doctor window had the same slip and now reads "Scheduled runs need fixing" in red.
- **The menu-bar panel's problem button opens Health.** It said "Open Settings" where Control Center says
  "Open Health". Failing, paused or held-back runs, a failed last run and engine warnings now open the
  Health page, from the panel and from the status bar. Settings stays the place for setting up, repairing a
  broken or unreadable engine, and allowing an agent to run.
- **The Schedules table fits at every Text size.** Its columns widen with the text, so "weekdays" no longer
  wraps to "weekda / ys" and the COOLDOWN header stays on one line. When the words don't fit, the days column
  shows the day dots alone, with the words as their tooltip. When the columns don't fit either, each schedule
  shows as a card. The table never scrolls sideways.
- **Sessions lanes show whole cards.** A lane wraps its cards onto more rows instead of scrolling sideways,
  which cut the last card off at 1280 and 1600 pt. Its Stale and Done buttons sit beside the lane's title.
  The detail pane appears only once a session is picked, and can always be closed, so the empty "Pick a
  session" pane no longer takes a quarter of the window.
- **Action Items has one "Done".** "Done" was both a status filter and a section chip. The section chip is
  gone; the status filter's Done shows the same completed list.
- **Buttons in a list no longer all show a focus ring.** While the Action Items list, the Schedules table or
  cards, or the KB file tree had keyboard focus (as Action Items does after Done), every button inside it drew
  a ring: without Full Keyboard Access a button takes its focus from the list around it. Now only a control
  that has focus itself draws one.
