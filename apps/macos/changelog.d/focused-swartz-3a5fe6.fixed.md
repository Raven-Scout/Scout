- **The Control Center tells one story about runs** (UI audit R10). The Today tile, the activity
  heatmap and Signals count failed runs the same way, and a routine skip shows amber, not as a red ✗.
  A day picked on the heatmap shows as a removable chip.
- **Signals point to the right place.** The usage-limit warning appears only when Scout's engine says a
  run hit the limit, and failed runs link to the Health page with their reason.
- **Long logs and diffs scroll smoothly**: they are drawn in pieces as you scroll.
- **Connector alerts are red only when a connector is down**, and **Acknowledge all** clears them. The
  connector health table names its columns by when each run started.
- **Failure notifications say why and offer Run again**, which reruns that run's own schedule. A
  notification updates in place when Scout learns more about how the run ended. Usage limits, skips,
  and runs not started by a schedule don't get Run again.
- **Install wake schedule asks first and says what happened**, with a command to copy when macOS needs
  an administrator. The menu-bar panel no longer cuts off the urgent count or the last run's time.
