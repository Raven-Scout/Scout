- **The agent-runtime interface is ready for a second runtime** (#652; `engine/scout/agent_runtimes/`). Nothing
  changes for a Claude Code run: its argv, environment, working directory, gate probe and sandbox profile are what
  they were.
  - **The launch environment.** A run on a runtime whose provider isn't Anthropic never gets `CLAUDE_*` or
    `ANTHROPIC_*` variables, such as the `CLAUDE_CODE_OAUTH_TOKEN` the retry wrapper exports. `exec` removes them
    whatever the adapter says. An adapter can also remove variables of its own and name a working directory
    (`Launch.env_unset`, `Launch.cwd`).
  - **The run timeout travels in the request** (`RunRequest.timeout_s`), so `exec` no longer asks the limits itself.
  - **Each runtime brings its own gate probe and sandbox paths.** The runners now resolve the runtime before the
    gate probe, and pass any runtime other than claude-code to `scoutctl gates probe --runtime` and
    `scoutctl sandbox profile --runtime`. The probe passes only when that runtime's adapter has proven every hook
    capability a run on the vault needs. The sandbox adds the adapter's protected trees and the folders under them
    that its CLI must write (`sandbox_paths`).
  - **Claude-specific code left the shared modules.** `check`'s interactive shortcut is the adapter's
    `interactive_host`, `limits` names `registry.BUILTIN_DEFAULT`, and `caps_own_spend` is now worked out from the
    adapter's `declared_capabilities` (`budget_cap`) instead of being a second flag.
  - **The owner's stores have their own protection label.** A write to provider consent, run limits or the other
    stores in `~/.local/state/scout/` is refused as "the owner's Scout settings", not "Scout's engine".
