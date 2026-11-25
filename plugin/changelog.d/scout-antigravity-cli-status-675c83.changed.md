- **The agent-runtime interface is ready for a second runtime** (#652; `engine/scout/agent_runtimes/`). A Claude Code
  run starts the same way: its argv, working directory, gate probe call and sandbox profile are what they were. Its
  environment gains only an empty `SCOUT_AGENT_RUNTIME_FALLBACK`, and the rows of runs skipped at the gate probe or
  the sandbox now name their runtime.
  - **The launch environment.** A runtime that isn't Anthropic's (by its registered id, not by what its adapter
    says) gets only an allowlist of the runner's environment: `PATH`, `HOME`, the account and locale, `XDG_*`,
    git's per-run `GIT_CONFIG_*` and `SCOUT_*` (`base.NON_ANTHROPIC_ENV_ALLOW`). So no `CLAUDE_CODE_OAUTH_TOKEN` or
    other `CLAUDE_*`/`ANTHROPIC_*` variable, no cloud or GitHub credentials, and no `SSH_AUTH_SOCK`. An adapter can
    remove more and add its own (`Launch.env_unset`, `Launch.env`), never an Anthropic one, and can name a
    working directory (`Launch.cwd`).
  - **The run timeout travels in the request** (`RunRequest.timeout_s`), so `exec` no longer asks the limits itself.
  - **Each runtime brings its own gate probe and sandbox paths.** The runners now resolve the runtime before the
    gate probe, and pass any runtime other than claude-code to `scoutctl gates probe --runtime` and
    `scoutctl sandbox profile --runtime`. The probe passes only when that runtime's adapter has proven every hook
    capability a run on the vault needs. The sandbox adds the adapter's protected trees and the folders under them
    that its CLI must write (`sandbox_paths`). A folder it asks to write must lie inside one of its own protected
    trees and may not hold, or sit inside, any of Scout's protected paths except another agent CLI's home folder
    (such as `~/.gemini`). Otherwise the profile isn't built and the run is skipped.
  - **An adapter can't declare a hook capability**: only a probe proves those, so registering one that does fails.
    The `claude-code` id can't be taken by another adapter.
  - **Claude-specific code left the shared modules.** `check`'s interactive shortcut is the adapter's
    `interactive_host`, `limits` names `registry.BUILTIN_DEFAULT`, and `caps_own_spend` is now worked out from the
    adapter's `declared_capabilities` (`budget_cap`) instead of being a second flag.
  - **The owner's stores have their own protection label.** A write to provider consent, run limits or the other
    stores in `~/.local/state/scout/` is refused as "the owner's Scout settings", not "Scout's engine".
