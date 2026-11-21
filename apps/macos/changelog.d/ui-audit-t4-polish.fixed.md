- **Settings and the Doctor window no longer call a problem healthy (UI audit T4).** Settings ▸ Engine showed
  "1 problem · 3 warnings" labelled "Healthy, with warnings", because it checked the install's verdict
  before the runs'. Its Health row now takes the worse of the two, in the Health page's words and colours:
  "Needs fixing" in red, "Needs a look" in amber, "Healthy" in green. A failing Runs row is red, not amber.
  The Doctor window had the same slip and now reads "Scheduled runs need fixing" in red. A broken engine or a
  health check that couldn't run is red in Settings as in the status bar. The Health page no longer says
  "Scheduled runs are healthy · 1 problem" when the problem is the engine's: it reads "Scout's engine needs
  fixing".
- **The menu-bar panel's problem button opens Health.** It said "Open Settings" where Control Center says
  "Open Health". Failing, paused or held-back runs, a failed last run and engine warnings now open the
  Health page, from the panel and from the status bar. Settings stays the place for setting up, repairing a
  broken or unreadable engine, and allowing an agent to run.
- **The Schedules table fits at every Text size.** Its columns widen with the text, so "weekdays" no longer
  wraps to "weekda / ys" and the COOLDOWN header stays on one line. When the words don't fit, the days column
  shows the day dots alone, with the words as their tooltip. When the columns don't fit either, each schedule
  shows as a card, keeping the table's 24-hour times. The table never scrolls sideways. The empty "Pick a
  schedule" pane no longer takes a third of the page: the detail pane opens with a schedule and can always be
  closed, so the table keeps its columns at 1280 pt.
- **Schedule cards never break a word.** At Largest text a narrow card broke "Consolidation" mid-word and grew
  taller than its neighbours. The type pill keeps one line, moving under the time when the two don't fit side
  by side, and cards in a row line up at the top.
- **Sessions lanes show whole cards.** A lane wraps its cards onto more rows instead of scrolling sideways,
  which cut the last card off at 1280 and 1600 pt. Its Stale and Done buttons sit beside the lane's title, and
  the cards they open start under a labelled rule. The Stale and Done state filters show those cards open:
  with "Show recently done" on, the Done filter left every lane empty, its cards folded away.
  The detail pane appears only once a session is picked, and can always be closed, so the empty "Pick a
  session" pane no longer takes a quarter of the window.
- **Action Items has one "Done".** "Done" was both a status filter and a section chip. The section chip is
  gone; the status filter's Done shows the same completed list.
- **Buttons in a list no longer all show a focus ring.** While the Action Items list, the Schedules table or
  cards, or the KB file tree had keyboard focus (as Action Items does after Done), every button inside it drew
  a ring: without Keyboard navigation a button takes its focus from the list around it. Now only a control
  that has focus itself draws one, and while the list holds the focus itself, its current card or row does,
  so Tab never stops on a list with nothing drawn.
