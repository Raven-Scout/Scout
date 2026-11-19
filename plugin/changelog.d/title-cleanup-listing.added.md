- **`scoutctl action-items check-titles --all`** lists every open action-item title over the 80-character
  budget, carried or new, in any section, longest first (`engine/scout/action_items/titles.py`). It is a
  read-only inventory for a one-off title cleanup, not a gate: it exits 0, or 2 when it cannot read the
  file. It prints tags and lengths, never a title. For each item it also shows when the item's block last
  changed and when the 60-day *Stale Items Auto-Archive* would take it, from an in-memory replay of the
  daily files; the cached activity ledger is neither read nor written. `--json` adds the summary counts
  in length bands (81–100, 101–120, 121+).
