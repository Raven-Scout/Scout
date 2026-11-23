- **A delegate mirror's `scout-config.yaml` holds only what the engine there reads (#550).** It was the vault's
  config with the `privacy` block rewritten, so every other key went verbatim, at every level: connector
  settings, tokens, addresses. Only a key that named a hidden note stayed home. The mirror's copy is now built
  from an allowlist, whether or not `privacy.sync_only` lists the config: the `privacy` block, `user.timezone`
  (when it names a real zone; the legacy top-level `timezone` counts), `user.name` and `user.display_name`, and
  `kb_structure.projects_dir` and `skip_projects` when they point at mirrored folders. Everything else stays
  home, `user.email` included, and a value that names a hidden note is dropped. The pre-push check rebuilds it
  and refuses any other copy.
- **The mirror's `privacy` block no longer carries `surfaces` or the `federation` keys.** No delegate's label
  or read depends on them: surfaces class the laptop's outbound checks, and the federation keys are for
  `agent:*` readers. They named chat ids and email addresses. The clone uses the engine defaults for them.
