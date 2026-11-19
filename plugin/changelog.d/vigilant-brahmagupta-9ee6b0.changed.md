- **Phase text uses the new session-context keys, and only when the file is from today.**
  - Research target selection (`phases/research/research-targets.md`) starts from `research_queue` and reads only
    the entries it will work on, instead of listing the queue and reading every entry. An entry with no `status:`
    now counts as work.
  - The Leave-State Compose Gate (`phases/core/action-items.md`) skips the lookup for someone `people_context.on_leave`
    lists. Everyone else still gets it, since not being listed proves nothing.
  - The leadership-ping rule (`phases/connectors/linear.md`) names `people_context.owner_managers`.
  - Project pages (`phases/core/kb-management.md`) say to read the knowledge notes an edit draws on before changing
    "Where it stands".

  The next release needs the KB-writing gate (`scripts/eval.sh kb-gate`).
