## Final Step: Telegram Copy of the Wrap (vault-local, required)

This is the last step of every briefing and consolidation run. The run isn't complete without it:
do it after the Slack wrap and before the session-cost step, even when the Slack wrap was reduced
or not sent.

1. Write the full wrap to `.scout-cache/telegram-wrap.md`. This chat is Alex's private Telegram
   chat (owner-only): name every urgent item there, private ones included, with their titles as
   they appear in the daily file.
2. Run `scripts/scoutctl notify telegram --tier info --body-file .scout-cache/telegram-wrap.md`.
3. Confirm it printed an `id=` receipt. If it didn't, fix the body and send it once more.
