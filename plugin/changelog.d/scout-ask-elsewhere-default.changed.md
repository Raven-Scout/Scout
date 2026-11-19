- **`scout-ask` hides private notes by default outside the vault** (`skills/scout-ask/SKILL.md`). When
  `kb vault --json` reports `in_vault: false`, every `kb` read the skill runs passes `--reader elsewhere`,
  so the engine, not the prompt, keeps private, local-only and shared-in notes and the phone inbox out of
  answers given in another repository. The skill drops the flag only when the user explicitly asks for
  their own private notes about their own life, and uses those only in its reply; the prose rule stays as
  a backstop. Inside the vault nothing changes. A session that already runs as a delegate or agent gets
  `--reader elsewhere` refused (exit 2), and the skill stops there rather than reading around it.
