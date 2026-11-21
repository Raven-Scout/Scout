- **Immediate responses to your Telegram messages** (opt-in; `engine/scout/telegram_inbound/`, `engine/scout/cli.py`). The trigger engine polls your bot for new messages, records each one in a ledger, and starts a short respond run (`RESPOND.md`, `run-respond.sh`) that replies in the chat, instead of waiting for the next scheduled run.
  - **Turn it on.** Run `/scout-update` first: it installs `RESPOND.md` and `run-respond.sh`. Then create `<vault>/.scout-state/triggers.yaml` with exactly:
    ```yaml
    schema_version: 1
    triggers:
      - id: telegram-respond
        source: telegram
        match: { type: message }
        action: { kind: run_skill, skill: scout-respond, runner: run-respond.sh }
        daily_fire_cap: 20
        cooldown_seconds: 600
    ```
  - **Turn it off** by deleting that file. Scout goes back to answering only at scheduled runs.
  - **`scoutctl telegram find-chat-id`** lists the private chats that have messaged your bot, without confirming any update. Use it instead of a raw `getUpdates` call, which comes back empty once the trigger has confirmed the updates.
  - **`scoutctl telegram inbound-status [--json]`** shows the ledger counts, the last poll time and the last poll error. `/scout-status` shows them when the trigger is on.
  - **`scoutctl telegram pending [--json] [--ids 5,6] [--exclude-in-flight]`** lists the messages still waiting for an answer, read-only. Runs read your messages only through it, never from a file a run can write.
  - **Known limits.**
    - The scheduled-run safety net, which picks up a message the trigger missed, needs `telegram` in `notify.wrap_channels`.
    - `scoutctl trigger test` on the Telegram trigger is not a dry run: it consumes and acknowledges real messages.
    - Private chats only, and text and captions only. Photos, voice notes and other attachments are ignored.
