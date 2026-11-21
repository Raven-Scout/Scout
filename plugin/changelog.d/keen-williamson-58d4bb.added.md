- **`scoutctl health resolve-prompt <kind> <key> [--json]` builds an investigation prompt for one alert**
  (`engine/scout/resolve_prompt.py`). The kind is `finding` (a run-health finding id, including
  suffixed ones such as `slot.stale:<key>`), `connector` (a `connector_key` from
  `connector-alerts.json`) or `run` (a run log's file name). It re-reads the live state and returns
  markdown in six sections: what Scout saw, the evidence to read (log, ledger row, schedule, the
  refs and commits an integrity check names, the engine's own code), how to investigate, the commands
  that reproduce it, a shared guardrail block (read-only first, no protected-file edits without asking,
  vault commits only through `scripts/git-safe-commit.sh`, never read secrets or `.env` files), and
  when it is done. Codes with their own playbook include the integrity checks, failing runs, sign-in,
  the sandbox, the privacy checks, the scheduler, slots and lanes, and updates; any other finding gets
  a generic one. A prompt carries paths, ids and SHAs, never a file's body. Read-only; exit 2 on a bad
  kind or key. The manifest advertises it as `resolve_prompt_v1`, and
  `tests/fixtures/contract/resolve-prompt.json` is its contract with the macOS app.
