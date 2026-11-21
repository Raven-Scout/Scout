- **"Resolve with Claude" on health, connector and run banners.** The Control Center's run-health and
  connector banners, the Health page's findings and a run's problem (failures, orphaned and
  rate-limited runs, and the safety skips, not routine skips) get a button that opens a new Claude Code
  session in Claude Desktop, in the vault, prefilled with an investigation prompt the engine builds
  (`scoutctl health resolve-prompt`). The chevron offers the terminal, chat and Cowork, as on action
  items. Nothing is sent until you send it. The button shows only on an engine that advertises
  `resolve_prompt_v1`, and the app refuses a prompt the engine built for a different vault folder.
