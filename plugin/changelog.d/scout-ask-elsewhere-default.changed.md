- **`scout-ask` hides private notes by default outside the vault** (`skills/scout-ask/SKILL.md`). When
  `kb vault --json` reports `in_vault: false`, every `kb` read the skill runs passes `--reader elsewhere`,
  so the engine, not the prompt, keeps private, local-only and shared-in notes and the phone inbox out of
  answers given in another repository, the graph queries (`query`, `related`, `traverse`, `path`)
  included. The skill drops the flag only when the user explicitly asks for their own private notes about
  their own life, only for the reads that answer that, and uses only those notes, only in its reply; the
  prose rule stays as a backstop. Inside the vault nothing changes. On exit 2 (a session that already runs
  as a delegate or agent, which refuses `--reader elsewhere`, a bad argument, or an engine too old to know
  `elsewhere`), the skill relays the engine's error and stops, never retrying without the flag.
