- **Phase text uses the new session-context keys.** Research target selection starts from `research_queue` and reads
  only the entries it will work on, instead of listing the queue and reading every entry
  (`phases/research/research-targets.md`). The Leave-State Compose Gate checks `people_context` first and looks a
  person up only when they aren't listed or have an active-status block (`phases/core/action-items.md`). The
  leadership-ping rule names `people_context.owner_managers` (`phases/connectors/linear.md`). Project pages say to read
  the knowledge notes an edit draws on before changing "Where it stands" (`phases/core/kb-management.md`). The next
  release needs the KB-writing gate (`scripts/eval.sh kb-gate`).
