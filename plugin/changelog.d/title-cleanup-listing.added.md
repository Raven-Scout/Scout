- **`scoutctl action-items check-titles --all`** lists every open action-item title over 80 characters,
  longest first, by tag and length (never the title), with when each item's 60-day stale clock runs out.
  It is read-only and exits 0 (`engine/scout/action_items/titles.py`).
- **`scoutctl action-items retitle`**, the owner's one-off title cleanup (`engine/scout/action_items/retitle.py`).
  - **Dry run (the default):** your own `claude` proposes shorter titles and each is checked against the
    title rule. The run writes only a worksheet you can edit, and prints its sha.
  - **`--apply <sha>`:** changes only the bold titles in today's file and keeps each original as
    `former_title` in the item's context note, in one commit.
  - **`--undo <commit> --yes`:** puts them back.
  - **Model call:** `claude` runs without settings files, hooks, tools or MCP servers. API keys and base
    URLs are removed from its environment, and every provider switch is set off.
- **Rewrite records keep an item's stale clock** (`engine/scout/action_items/stale.py`):
  `.scout-state/title-rewrites.jsonl` names a block change that isn't a touch, and only an exact match
  counts. Scheduled runs can't write the file: it is a protected path.
