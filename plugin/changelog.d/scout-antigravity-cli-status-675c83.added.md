- **Run health says when a slot ran on Claude Code instead of the runtime set for it** (#652 S7;
  `engine/scout/health.py`, `templates/run-*.sh.tmpl`, `templates/scripts/run-outcome.sh.tmpl`). When the runner
  can't resolve a slot's agent runtime, it still falls back to claude-code, and the run's ledger row now records
  `agent_runtime_fallback: resolve_failed`. Until a later run of that slot resolves its runtime, run health shows the
  yellow finding `agent_runtime.fallback`, naming the slots and how to see the error. Before, only the run log said so.
