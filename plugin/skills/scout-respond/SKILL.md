---
name: scout-respond
description: Answer pending Telegram messages from the owner now (normally started automatically by the telegram-respond trigger)
---

Launch a SCOUT respond session as a background process on this machine. The telegram-respond trigger normally starts this run within minutes of a message; run it by hand only to answer pending messages now.

Do the following steps, from the vault directory:

1. Run `nohup bash ./run-respond.sh > /dev/null 2>&1 &` to launch the respond session in the background. Capture the PID.

2. Find the most recent log file: `ls -t .scout-logs/respond-*.log | head -1`

3. Report to the user:
   - Confirm the respond session is running and show the PID
   - Show the log file path so they can tail it if they want: `tail -f <log_file>`
   - Remind them that a respond run is short: it reads the pending Telegram messages, does what they ask within Scout's usual guardrails, commits, and sends one Telegram reply. Started by hand, it answers every message still waiting (what `scoutctl telegram pending` lists); with none, it sends nothing.
