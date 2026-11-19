- **The Control Center counts and colours runs one way** (UI audit R10, #21). The Today tile, the
  activity heatmap and the Signals card count failures with one rule, `RunOutcomeDisplay.isFailure`
  (the run ledger's failed outcome: timeouts and usage limits included, skips and unfinished runs
  not), on the same day. The Now column paints a routine skip amber with a pause, not a red ✗.
- **A day picked on the heatmap shows as a "Runs on Nov 12" chip** that clears the filter (#22).
- **Signals follow the engine** (#23). The usage-limit row fires only on a run the engine classed
  `rate_limited`, never on a log line that mentions "429" or "rate", and no longer says "top-up may be
  needed". Failed runs name their shared reason (a day of sign-in races, `auth_race`, reads amber) and
  open the Health page instead of pointing at Sessions. The connector-roster note is said once, on the
  Connector health card (#30).
- **Big logs and diffs scroll smoothly** (#25). The log viewer and the run's diffs render in chunks of
  200 lines, laid out as they scroll in, instead of one text view per log or diff.
- **The connector alert banner is red only for a critical alert** (#28): warnings are an amber wash,
  the worst alert leads, and the popover scrolls and has **Acknowledge all**. The connector health
  table heads its columns with when each run started ("8 AM", "Tue") instead of r1–r5, labels the rate
  column "Worked", and lists only the connectors those runs used (#29).
- **Failure notifications say why and offer Run again** (#96). The body names the run ledger's failure
  class ("Claude login was busy"), updating quietly when the ledger lands after the log, and **Run
  again** fires the run's own schedule, as Run again in the run's detail does. Event-triggered and
  manual runs, and skips, get no Run again.
- **Install wake schedule asks first and reports what happened** (#4). It used to run under `try?` and
  say nothing; the menu-bar panel now confirms, then shows "Wake schedule installed" or why not, with
  the exact `sudo pmset …` command when macOS needs an administrator.
- **The menu-bar panel no longer truncates** "23 urg…" or "Last: … 1 ho…": the urgent chip keeps its
  size, the Open button drops its word when space is short, and the status line wraps.
