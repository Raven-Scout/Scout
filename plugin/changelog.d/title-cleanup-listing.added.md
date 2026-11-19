- **`scoutctl action-items check-titles --all`** lists every open action-item title over the 80-character
  budget, carried or new, in any section, longest first (`engine/scout/action_items/titles.py`). It is a
  read-only inventory for a one-off title cleanup, not a gate: it exits 0, or 2 when it cannot read the
  file. It prints tags and lengths, never a title. For each item it also shows when the item's block last
  changed and when the 60-day *Stale Items Auto-Archive* would take it, from an in-memory replay of the
  daily files; the cached activity ledger is neither read nor written. `--json` adds the summary counts
  in length bands (81–100, 101–120, 121+).
- **`scoutctl action-items retitle`**: the owner's one-off title cleanup, which shortens every open title
  over 80 characters without restarting the items' 60-day stale clocks (`engine/scout/action_items/retitle.py`).
  - **Owner only.** It refuses in a scheduled run (`SCOUT_MODE`, or a `SCOUT_READER` that isn't the
    owner), without a terminal, and while a run of the vault is live.
  - **Dry run first.** The default run asks the owner's own `claude` login for proposals. Every API-key
    and cloud-provider variable is removed from its environment; it gets no tools and no MCP servers, and
    the items go in on stdin. Each proposal is checked against *Clean Title, Prose Body, Refs Block*. The
    proposals are saved to a worksheet in `.scout-cache/retitle/`, which the owner may edit, and the run
    prints a short sha of it. Nothing else is written.
  - **`--apply <sha>`** applies exactly that worksheet. It changes only the bold titles in today's file,
    keeps each original word for word as `former_title` in the item's context note, and makes one commit
    with the vault's `scripts/git-safe-commit.sh`. If the commit fails, every file is put back.
  - **Items it leaves out:** an item the archive takes within 7 days (`--skip-archiving-within`), a tag
    on more than one row, and an item already shortened once. `--section` limits the run to one section.
  - **Undo** with `--undo <commit> --yes`, not `git revert`.
- **Rewrite records keep an item's stale clock** (`engine/scout/action_items/stale.py`). `retitle` records
  each rewrite in `.scout-state/title-rewrites.jsonl`, committed with the vault: the tag, the day, and the
  block's fingerprint before and after. The activity ledger keeps the item's date only when that day's
  change matches the record exactly, so any other edit still restarts the clock. A rebuilt cache gives
  the same answer. Scheduled runs never write records.
